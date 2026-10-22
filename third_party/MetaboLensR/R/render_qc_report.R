#' Render a standalone QC report
#'
#' @param experiment A finalized SummarizedExperiment.
#' @param output_file Destination HTML path in an existing directory.
#' @param default_assay Assay shown first in the report.
#'
#' @return Invisibly returns `output_file`.
#' @export
render_qc_report <- function(
  experiment,
  output_file,
  default_assay = "intensity"
) {
  validate_experiment(experiment)

  if(!dir.exists(dirname(output_file))) {
    stop(
      "the QC report output directory does not exist"
    )
  }

  normalization_summary <-
    S4Vectors::metadata(
      experiment
    )$normalization_summary

  available_assays <-
    normalization_summary$assay[
      normalization_summary$status == "available"
    ]

  if (!default_assay %in% available_assays) {
    stop(
      "default_assay must be available in the finalized report",
      call. = FALSE
    )
  }

  template <- system.file(
    "quarto",
    "qc_report.qmd",
    package = "MetaboLensR"
  )

  if (!nzchar(template)) {
    stop(
      "The QC report template was not found"
    )
  }

  assay_order <- c(
    default_assay,
    setdiff(
      available_assays,
      default_assay
    )
  )

  render_dir <- tempfile(
    "metabolensr-report"
  )

  dir.create(render_dir)

  on.exit(
    unlink(
      render_dir,
      recursive = TRUE
    ),
    add = TRUE
  )

  payload_path <- file.path(
    render_dir,
    "qc_report_payload.rds"
  )

  template_path <- file.path(
    render_dir,
    "qc_report.qmd"
  )

  payload <- list(
    experiment = experiment,
    normalization_summary = 
      normalization_summary,
    feature_data = as.data.frame(
      SummarizedExperiment::rowData(
        experiment
      ),
      optional = TRUE
    ),
    sample_data = as.data.frame(
      SummarizedExperiment::colData(
        experiment
      ),
      optional = TRUE
    ),
    default_assay = default_assay,
    assay_order = assay_order
  )

  saveRDS(
    payload,
    payload_path
  )

  file.copy(
    template,
    template_path
  )

  quarto::quarto_render(
    input = template_path,
    output_format = "html",
    output_file = "qc_report.html",
    execute_params = list(
      payload_path = payload_path
    ),
    execute_dir =  render_dir,
    quiet = TRUE
  )

  rendered_path <- file.path(
    render_dir,
    "qc_report.html"
  )

  if (!file.exists(rendered_path)) {
    stop(
      "Quarto did not create the expected QC report.",
      call. = FALSE
    )
  }

  file.copy(
    rendered_path,
    output_file,
    overwrite = TRUE
  )



  invisible(output_file)
}