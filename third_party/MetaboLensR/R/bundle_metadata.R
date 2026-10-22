.input_record <- function(path) {
  list(file = basename(path), md5 = unname(tools::md5sum(path)))
}

.bundle_metadata <- function(experiment, bundle_type, paths) {
  metadata <- S4Vectors::metadata(experiment)
  list(
    schema_version = "2.0",
    experiment_schema_version = metadata$schema_version,
    bundle_type = bundle_type,
    package_version = as.character(utils::packageVersion("MetaboLensR")),
    r_version = R.version.string,
    source = metadata$source,
    configuration = metadata$configuration,
    inputs = metadata$inputs,
    curation = metadata$curation,
    matrix_feature_scope = "all_features",
    available_assays = unname(as.list(SummarizedExperiment::assayNames(experiment))),
    outputs = data.frame(role = names(paths), file = basename(unname(paths)),
                         md5 = unname(tools::md5sum(paths)), stringsAsFactors = FALSE)
  )
}

.write_sample_metadata <- function(experiment, output_dir) {
  path <- file.path(output_dir, "sample_metadata.tsv")
  utils::write.table(as.data.frame(SummarizedExperiment::colData(experiment)),
                     path, sep = "\t", quote = TRUE, qmethod = "double", row.names = FALSE,
                     na = "", fileEncoding = "UTF-8")
  path
}
