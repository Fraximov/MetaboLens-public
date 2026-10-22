#!/usr/bin/env Rscript

script_arg <- grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE)[[1]]
workflow_root <- normalizePath(file.path(dirname(normalizePath(sub("^--file=", "", script_arg))), "..", ".."), mustWork = TRUE)
source(file.path(workflow_root, "R/functions/utils.R"))

args <- statpipeline_parse_args()
input <- statpipeline_arg(args, "input", required = TRUE)
outdir <- statpipeline_arg(args, "outdir", required = TRUE)
outdir <- statpipeline_ensure_dir(outdir)

previous_wd <- getwd()
on.exit(setwd(previous_wd), add = TRUE)
setwd(outdir)

legacy_script <- file.path(workflow_root, "R", "legacy", "StatPipeline_v2.R")
file.copy(input, file.path(outdir, basename(input)), overwrite = TRUE)
source(legacy_script, local = .GlobalEnv)
