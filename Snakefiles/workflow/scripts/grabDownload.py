#bin/bash 

import sys, os
import argparse 
import subprocess
import numpy as np
import pandas as pd
import itertools
from utils import *
from multiprocessing import Pool
from subprocess import check_call, check_output, PIPE, Popen, getoutput, CalledProcessError

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dhs', help="DNase experiments file")
    parser.add_argument('--dhs_fastq', help="DNase experiments file fastq metadata")
    parser.add_argument('--h3k27ac', help="H3K27ac experiments file")
    parser.add_argument('--h3k27ac_fastq', help="H3K27ac experiments file fastq metadata")
    parser.add_argument('--atac', help="ATAC experiments file")
    parser.add_argument('--atac_fastq', help="ATAC experiments file fastq metadata")
    parser.add_argument('--expt_file', help="File that holds all experimental name and summary from ENCODE")
    parser.add_argument('--genome_assembly', help="Genome assembly")
    parser.add_argument('--download_files', action='store_true', help="Flag to download files or not")
    parser.add_argument('--outdir', default=".", help="Outdir to save downloaded metadata files")
    parser.add_argument('--data_outdir', help="Outdir to save downloaded bam files")
    parser.add_argument('--apply_pool', action='store_true', help="Apply multiprocessing Pool")
    parser.add_argument('--threads', default=1, help="Number of threads")
    args = parser.parse_args()
    return args 

def main():
    args = parse_args()
    metadata = assignFiltersToDataFrame(args)
    if args.download_files:
        outfile = downloadFiles(args, metadata)
    save_paired_single_end_files(args, metadata)

def load_data(bam, fastq):
    """
    Loads data
    """
    print("Opening DHS and H3K27ac Experiment Files...")
    dhs_data = pd.read_csv(bam, sep="\t")
    print("Opening DHS and H3K27ac Fastq Experiment Files...")
    dhs_fastq = pd.read_csv(fastq, sep="\t")
    return dhs_data, dhs_fastq 

def assignFiltersToDataFrame(args):
    # open dataframes 
    # load data 
    experiment_metadata = pd.read_csv(args.expt_file, sep="\t", header=None)
    dhs_data, dhs_fastq = load_data(args.dhs, args.dhs_fastq)
    dhs_alignment_bam = mapExperimentToLength(dhs_data, dhs_fastq)
    merge_columns = ['Biosample term name','Biosample organism', 'Biosample treatments','Biosample treatments amount', 'Biosample treatments duration','Biosample genetic modifications methods','Biosample genetic modifications categories','Biosample genetic modifications targets', 'Biosample genetic modifications gene targets', 'File assembly', 'Genome annotation', 'File format', 'File type', 'Output type', 'Lab', 'Biosample summary']
    dhs_alignment_bam = rename_biosample(experiment_metadata, dhs_alignment_bam)    
    if args.h3k27ac is not None and args.h3k27ac_fastq is not None:
        h3k27ac_data, h3k27ac_fastq = load_data(args.h3k27ac, args.h3k27ac_fastq)
        h3k27ac_alignment_bam = mapExperimentToLength(h3k27ac_data, h3k27ac_fastq)
        dhs_alignment_bam['Biosample genetic modifications methods'] = dhs_alignment_bam['Biosample genetic modifications methods'].astype('str').fillna(0.0)
        h3k27ac_alignment_bam['Biosample genetic modifications methods'] = h3k27ac_alignment_bam['Biosample genetic modifications methods'].astype('str').fillna(0.0)
        h3k27ac_alignment_bam = rename_biosample(experiment_metadata,h3k27ac_alignment_bam)
        intersected = pd.merge(dhs_alignment_bam, h3k27ac_alignment_bam, how='inner', on=merge_columns, suffixes=('_Accessibility', '_H3K27ac'))
    else:
        intersected_columns = list(dhs_alignment_bam.columns)
        columns_to_rename = [col for col in intersected_columns if col not in merge_columns]
        intersected = dhs_alignment_bam.rename(columns={key:value+"_Accessibility" for key,value in zip(list(columns_to_rename), list(columns_to_rename))})

    if args.atac is not None and args.atac_fastq is not None:
        atac_df = pd.read_csv(args.atac, sep="\t")
        atac_fastq = pd.read_csv(args.atac_fastq, sep="\t")
        atac = mapExperimentToLength(atac_df, atac_fastq)
        atac = rename_biosample(experiment_metadata, atac)
        intersected_df = pd.merge(atac, h3k27ac_alignment_bam, how='inner', on=merge_columns, suffixes=('_Accessibility', '_H3K27ac'))
        copy = intersected
        intersected = pd.concat([copy, intersected_df])
    # filter for filtered file + released files 
    # fill columns that are filled with NAN
    df = intersected.fillna(0.0)
    # grab entries with biological replicates 
    # grab celltypes with biological replicates 
    full_metadata, metadata_unique = obtainDuplicated(args, df)

    # grab entries with no biological repliates 
    # filter for mapped read lengths of usually 32.0 or 36.0
    
    # metadata_unique contains the combined accession numbers for experiments with technical/biological replicates 
    # full metadata contains every technical and biological replicate as its own single entry
    # save relevant columns into input data lookup for input into ABC code
    prepareLookup(args, full_metadata, "input_data_lookup")
    return full_metadata

def rename_biosample(expt_file, data): 
    data['Biosample summary'] = data['Biosample term name'] 
    data['Biosample stage'] = 'unknown'
    for expt in data['Experiment accession']:
        orig_matched_index = data.loc[data['Experiment accession']==expt].index.astype('int')
        matched = expt_file.loc[expt_file[1]==expt]
        if len(matched) > 0:
            name = matched[6].values[0]
            stage = matched[20].values[0]
            biosample_name = str(name).replace(" ", "_")
            data.loc[orig_matched_index, 'Biosample summary'] = biosample_name
            data.loc[orig_matched_index, 'Biosample stage'] = stage
    return data

def download_single_bam(bam):
    command = "wget {} -P {}".format(bam[0], bam[1])
    p = Popen(command, stdout=PIPE, stderr=PIPE, shell=True)
    print("Running: " + command)
    (stdoutdata, stderrdata) = p.communicate()
    err = str(stderrdata, 'utf-8')
    return stdoutdata

def downloadFiles(args, df):
    # get download links and start downloading 
    if args.h3k27ac is not None:
        download_links = df[['File download URL_Accessibility', 'File download URL_H3K27ac']].melt(value_name='download_links').drop_duplicates()
    else:
        download_links = df[['File download URL_Accessibility']].melt(value_name='download_links').drop_duplicates()
    outfile=os.path.join(args.outdir, "linkstodownload.txt")
    download_links[['download_links']].to_csv( outfile, sep="\t", index=False, header=None)
    
    if not os.path.exists(args.data_outdir):
        os.mkdir(args.data_outdir)
    if args.apply_pool:
        with Pool(int(args.threads)) as p:
            p.map(download_single_bam, zip(list(download_links['download_links']), itertools.repeat(args.data_outdir))) 
    else:
        for link in list(download_links['download_links']):
            download_single_bam(link)
    return outfile 

def save_paired_single_end_files(args, metadata):
    for col, title in zip(["single-ended", "paired-ended"], ["singleend", "pairedend"]):
        paired = metadata['File accession_Accessibility'].loc[metadata['Run type_Accessibility']==str(col)].drop_duplicates()
        if args.h3k27ac is not None:
            paired_h3k27ac = metadata['File accession_H3K27ac'].loc[metadata['Run type_H3K27ac']==str(col)].drop_duplicates()
            combined_paired = pd.concat([paired, paired_h3k27ac])
            combined_paired.to_csv(os.path.join(args.outdir, "unique_{}_h3k27ac_dhs_files.tsv".format(str(title))), sep="\t", index=False)
        else:
            combined_paired = paired
            combined_paired.to_csv(os.path.join(args.outdir, "unique_{}_h3k27ac_dhs_files.tsv".format(str(title))), sep="\t", index=False)

if __name__=="__main__":
    main()
