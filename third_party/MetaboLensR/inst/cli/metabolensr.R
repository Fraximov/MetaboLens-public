main <- function(args) {
  usage <- paste(
    "Usage: metabolensr <command> [options]",
    "",
    "Commands:",
    "  preview    Generate the automatic QC review bundle",
    "  finalize   Apply overrides and generate results and HTML",
    "",
    "Run metabolensr <command> --help for options.",
    sep = "\n"
  )

  if (
    length(args) == 0L ||
    args[[1L]] %in% c("-h", "--help")
  ) {
    cat(usage, "\n")
    return(invisible(NULL))
  }

  command <- args[[1L]]

  option_help <- switch(
    command,
    preview = c(
      input = "Path to the aligned table for the configured source_tool",
      config = "Path to the JSON configuration",
      outdir = "Directory for the preview bundle",
      samplesheet = "CSV acquisition metadata (required for MassCube)"
    ),
    finalize = c(
      preview = "Path to the QC preview RDS",
      overrides = "Path to the operator override TSV",
      outdir = "Directory for results and HTML"
    ),
    stop("Unknown command: ", command, call. = FALSE)
  )

  parser <- optparse::OptionParser(
    usage = paste("metabolensr", command, "[options]"),
    option_list = lapply(
      names(option_help),
      function(name) {
        optparse::make_option(
          paste0("--", name),
          type = "character",
          help = option_help[[name]]
        )
      }
    )
  )

  options <- optparse::parse_args(
    parser,
    args = args[-1L]
  )

  for (name in setdiff(names(option_help), "samplesheet")) {
    value <- options[[name]]

    if (is.null(value) || !nzchar(trimws(value))) {
      stop(
        "Missing required option: --",
        name,
        call. = FALSE
      )
    }
  }

  paths <- switch(
    command,
    preview = MetaboLensR::run_qc_preview(
      input = options$input,
      config = options$config,
      output_dir = options$outdir,
      samplesheet = options$samplesheet
    ),
    finalize = MetaboLensR::run_qc_finalize(
      preview_path = options$preview,
      overrides = options$overrides,
      output_dir = options$outdir
    )
  )

  cat("QC", command, "written:\n")
  cat(paste(unname(paths), collapse = "\n"), "\n")
}

tryCatch(
  main(commandArgs(trailingOnly = TRUE)),
  error = function(error) {
    message("MetaboLensR: ", conditionMessage(error))
    quit(save = "no", status = 1L)
  }
)
