#!/usr/bin/env python
import argparse
import csv
import shlex
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor


from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord

parser = argparse.ArgumentParser(description='''
             Prepartion of repetive element pseudogenomes bowtie\
             indexes and annotation files used by RepEnrich.py enrichment.''',
                                 prog='getargs_genome_maker.py')
parser.add_argument('--annotation_file', action='store',
                    metavar='annotation_file',
                    help='''Repeat masker annotation of the genome of\
                         interest. Download from RepeatMasker.org\
                         Example: mm9.fa.out''')
parser.add_argument('--genomefasta', action='store', metavar='genomefasta',
                    help='''Genome of interest in fasta format.\
                         Example: mm9.fa''')
parser.add_argument('--gaplength', action='store', dest='gaplength',
                    metavar='gaplength', default='200', type=int,
                    help='''Length of the N-spacer in the\
                         repeat pseudogenomes.  Default 200''')
parser.add_argument('--flankinglength', action='store', dest='flankinglength',
                    metavar='flankinglength', default='25', type=int,
                    help='''Length of the flanking regions used to build\
                         repeat pseudogenomes. Flanking length should be set\
                         according to the length of your reads.\
                         Default 25, for 50 nt reads''')
parser.add_argument('--cpus', action='store', dest='cpus', metavar='cpus',
                    default="1", type=int,
                    help='Number of CPUs. The more cpus the\
                          faster RepEnrich performs. Default: "1"')
args = parser.parse_args()

# parameters from argsparse
gapl = args.gaplength
flankingl = args.flankinglength
annotation_file = args.annotation_file
genomefasta = args.genomefasta
cpus = args.cpus


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


# load genome into dictionary and compute length
g = SeqIO.to_dict(SeqIO.parse(genomefasta, "fasta"))
genome = defaultdict(dict)

for chr in g.keys():
    genome[chr]['sequence'] = str(g[chr].seq)
    genome[chr]['length'] = len(g[chr].seq)

# Build a bedfile of repeatcoordinates to use by RepEnrich region_sorter
repeat_elements = set()
rep_coords = defaultdict(list)  # Merged dictionary for coordinates

with open('repnames.bed', 'w') as fout:
    f_in = import_text(annotation_file, ' ')
    for line in f_in:
        repname = line[9].translate(str.maketrans('()/', '___'))
        repeat_elements.add(repname)
        repchr, repstart, repend = line[4], line[5], line[6]
        fout.write(f"{repchr}\t{repstart}\t{repend}\t{repname}\n")
        rep_coords[repname].extend([repchr, repstart, repend])
# repeat_elements now contains the unique repeat names
# rep_coords is a dictionary where keys are repeat names and values are lists
# containing chromosome, start, and end coordinates for each repeat instance

# sort repeat_elements and print them in repeatIDs.txt
with open('repeatIDs.txt', 'w') as fout:
    for i, repeat in enumerate(sorted(repeat_elements)):
        fout.write('\t'.join([repeat, str(i)]) + '\n')

# generate spacer for pseudogenomes
spacer = ''.join(['N' for i in range(gapl)])

# generate metagenomes and save them to FASTA files for bowtie build
for repname in rep_coords:
    genomes_list = []
    # iterating coordinate list by block of 3 (chr, start, end)
    block = 3
    for i in range(0, len(rep_coords[repname]) - block + 1, block):
        batch = rep_coords[repname][i:i+block]
        chromosome = batch[0]
        start = max(int(batch[1]) - flankingl, 0)
        end = min(int(batch[2]) + flankingl,
                  int(genome[chromosome]['length'])-1) + 1
        genomes_list.append(genome[chromosome]['sequence'][start:end])
    metagenome = spacer.join(genomes_list)
    # Create Fasta of repeat pseudogenome
    fastafilename = f"{repname}.fa"
    record = SeqRecord(Seq(metagenome), id=repname, name='', description='')
    SeqIO.write(record, fastafilename, "fasta")


def bowtie_build(args):
    """
    Function to be executed in parallel by ProcessPoolExecutor.
    """
    try:
        bowtie_base, fasta = args
        command = shlex.split(f"bowtie-build -f {fasta} {bowtie_base}")
        squash = subprocess.run(command, capture_output=True, text=True)
        return squash.stdout
    except Exception as e:
        return str(e)


args_list = [(name, f"{name}.fa") for name in rep_coords]
with ProcessPoolExecutor(max_workers=cpus) as executor:
    executor.map(bowtie_build, args_list)
