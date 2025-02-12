import argparse
import csv
import os
import shlex
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor


parser = argparse.ArgumentParser(description='''
             Repenrich aligns reads to Repeat Elements pseudogenomes\
             and counts aligned reads. RepEnrich_setup must be run\
             before its use''')
parser.add_argument('--annotation_file', action='store',
                    metavar='annotation_file',
                    help='RepeatMasker.org annotation file for your\
                          organism. The file may be downloaded from\
                          RepeatMasker.org. E.g. hg19_repeatmasker.txt')
parser.add_argument('--alignment_bam', action='store', metavar='alignment_bam',
                    help='Bam alignments of unique mapper reads.')
parser.add_argument('--fastqfile', action='store', metavar='fastqfile',
                    help='File of fastq reads mapping to multiple\
                          locations. Example: /data/multimap.fastq')
parser.add_argument('--fastqfile2', action='store', dest='fastqfile2',
                    metavar='fastqfile2', default='',
                    help='fastqfile #2 when using paired-end option.\
                          Default none')
parser.add_argument('--cpus', action='store', dest='cpus', metavar='cpus',
                    default="1", type=int,
                    help='Number of CPUs. The more cpus the\
                          faster RepEnrich performs. Default: "1"')

args = parser.parse_args()

# parameters
annotation_file = args.annotation_file
unique_mapper_bam = args.alignment_bam
fastqfile_1 = args.fastqfile
fastqfile_2 = args.fastqfile2
cpus = args.cpus
# Change if simple repeats are differently annotated in your organism
simple_repeat = "Simple_repeat"
if args.fastqfile2:
    paired_end = True
else:
    paired_end = False

# check that the programs we need are available
try:
    subprocess.call(shlex.split("coverageBed -h"),
                    stdout=open(os.devnull, 'wb'),
                    stderr=open(os.devnull, 'wb'))
    subprocess.call(shlex.split("bowtie2 --version"),
                    stdout=open(os.devnull, 'wb'),
                    stderr=open(os.devnull, 'wb'))
except OSError:
    print("Error: Bowtie2 or bedtools not loaded")
    raise


def starts_with_numerical(list):
    try:
        if len(list) == 0:
            return False
        int(list[0])
        return True
    except ValueError:
        return False


# define a text importer for .out/.txt format of repbase
def import_text(filename, separator):
    csv.field_size_limit(sys.maxsize)
    file = csv.reader(open(filename), delimiter=separator,
                      skipinitialspace=True)
    return [line for line in file if starts_with_numerical(line)]


# set a reference repeat list for the script
repeat_list = [listline[9].translate(
    str.maketrans(
        '()/', '___')) for listline in import_text(annotation_file, ' ')]
repeat_list = sorted(list(set(repeat_list)))

# unique mapper counting
cmd = f"bedtools bamtobed -i {unique_mapper_bam} | \
        bedtools coverage -b stdin -a  repnames.bed"
p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
bedtools_counts = p.communicate()[0].decode().rstrip('\r\n').split('\n')

# parse bedtools output
counts = defaultdict(int)  # key: repeat names, value: unique mapper counts
sumofrepeatreads = 0
for line in bedtools_counts:
    line = line.split('\t')
    counts[line[3]] += int(line[4])
    sumofrepeatreads += int(line[4])
print(f"Identified {sumofrepeatreads} unique reads that mapped to repeats.")

# print unique mapper counts
with open("unique_mapper_counts.tsv", 'w') as fout:
    fout.write("#element\tcount\n")
    for count in sorted(counts):
        fout.write(f"{count}\t{counts[count]}\n")


def run_bowtie(args):
    '''
    write to files to save memory
    '''
    metagenome = args
    b_opt = "-k 1 -p 2 --quiet --no-hd --no-unal"
    if paired_end is True:
        command = shlex.split(f"bowtie2 {b_opt} -x {metagenome}"
                              f" -1 {fastqfile_1} -2 {fastqfile_1}")
    else:
        command = shlex.split(f"bowtie2 {b_opt} -x {metagenome} {fastqfile_1}")
    bowtie_align = subprocess.run(command, check=True,
                                  capture_output=True, text=True).stdout
    bowtie_align = bowtie_align.rstrip('\r\n').split('\n')
    with open(f"{metagenome}.reads", "a+") as readfile:
        for line in bowtie_align:
            read = line.split()[0].split("/")[0]
            if read:
                readfile.write(f"{read}\n")


# multimapper parsing
args_list = [metagenome for metagenome in repeat_list]
with ProcessPoolExecutor(max_workers=cpus) as executor:
    results = executor.map(run_bowtie, args_list)

# Aggregate results (avoiding race conditions)
metagenome_reads = defaultdict(list)  # metagenome: list of multimap reads

# Now we read .reads files to populate metagnomes_reads
for metagenome in repeat_list:
    with open(f"{metagenome}.reads") as readfile:
        for read in readfile:
            metagenome_reads[metagenome].append(read)
        # read are only once in list
        metagenome_reads[metagenome] = list(set(metagenome_reads[metagenome]))

# implement repeats_by_reads from the inverse dictionnary metagenome_reads
repeats_by_reads = defaultdict(list)  # readids: list of repeats names
for repname in metagenome_reads:
    for read in metagenome_reads[repname]:
        repeats_by_reads[read].append(repname)
for repname in repeats_by_reads:
    repeats_by_reads[repname] = list(set(repeats_by_reads[repname]))
    # this repeats_by_reads dictionary is far too big

# 3 dictionnaries and 1 pointer variable to be populated
fractionalcounts = defaultdict(float)
familyfractionalcounts = defaultdict(float)
classfractionalcounts = defaultdict(float)
sumofrepeatreads = 0

# Update counts dictionnary with sets of repeats (was "subfamilies")
# matched by multimappers
for repeat_set in repeats_by_reads.values():
    repeat_set_string = ','.join(repeat_set)
    counts[repeat_set_string] += 1
    sumofrepeatreads += 1

print(f'Identified more {sumofrepeatreads} mutimapper repeat reads')

# Populate fractionalcounts
for key, count in counts.items():
    key_list = key.split(',')
    for i in key_list:
        fractionalcounts[i] += count / len(key_list)

# build repeat_ref for easy access to rep class and rep families
repeat_ref = defaultdict(dict)
repeats = import_text(annotation_file, ' ')
for repeat in repeats:
    repeat_name = repeat[9].translate(str.maketrans('()/', '___'))
    try:
        repclass = repeat[10].split('/')[0]
        repfamily = repeat[10].split('/')[1]
    except IndexError:
        repclass, repfamily = repeat[10], repeat[10]
    repeat_ref[repeat_name]['class'] = repclass
    repeat_ref[repeat_name]['family'] = repfamily

# Populate classfractionalcounts and familyfractionalcounts
for key, value in fractionalcounts.items():
    classfractionalcounts[repeat_ref[key]['class']] += value
    familyfractionalcounts[repeat_ref[key]['family']] += value

# print class-, family- and fraction-repeats counts to files
with open("class_fraction_counts.tsv", 'w') as fout:
    for key in sorted(classfractionalcounts):
        fout.write(f"{key}\t{round(classfractionalcounts[key], 2)}\n")

with open("family_fraction_counts.tsv", 'w') as fout:
    for key in sorted(familyfractionalcounts):
        fout.write(f"{key}\t{round(familyfractionalcounts[key], 2)}\n")

with open("fraction_counts.tsv", 'w') as fout:
    for key in sorted(fractionalcounts):
        fout.write(f"{key}\t{repeat_ref[key]['class']}\t"
                   f"{repeat_ref[key]['family']}\t"
                   f"{round(fractionalcounts[key], 2)}\n")
