# Regression check for the container's interactive report dependency.
# Debian's plotly 4.10.1 declares JS 2.11.1 but ships JS 1.31.2.
plot <- plotly::plot_ly(
  x = 1:3, y = c(100, 120, 110), type = "scatter", mode = "markers"
)
dependency <- Filter(
  function(x) identical(x$name, "plotly-main"),
  plotly::plotly_build(plot)$dependencies
)[[1L]]
script <- system.file(
  dependency$src$file, dependency$script,
  package = dependency$package, mustWork = TRUE
)
header <- readLines(script, n = 2L, warn = FALSE)[[2L]]
actual <- sub("^\\* plotly\\.js v", "", trimws(header))
if (!identical(actual, dependency$version)) {
  stop(sprintf(
    "Plotly JavaScript mismatch: R declares %s but the bundled script is %s",
    dependency$version, actual
  ))
}
message("Plotly JavaScript matches R dependency: ", actual)
