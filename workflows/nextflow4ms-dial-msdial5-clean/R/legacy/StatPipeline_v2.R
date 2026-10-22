# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  MS-DIAL Statistical Analysis & Normalization Pipeline v2.0                ║
# ║  Core Facility Edition                                                      ║
# ║                                                                             ║
# ║  For lipidomics and metabolomics data exported from MS-DIAL                ║
# ║  Place this script in a folder containing one MS-DIAL alignment .txt file  ║
# ║  Set parameters below, then source() or run the entire script.             ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ══════════════════════════════════════════════════════════════════════════════
# 1. PARAMETERS
# ══════════════════════════════════════════════════════════════════════════════

# Data type ─────────────────────────────────────────────────────────────────
data_type <- "Lipidomics"    # "Lipidomics" or "Metabolomics"

# Preprocessing ─────────────────────────────────────────────────────────────
remove_unknowns        <- TRUE
remove_blank_features  <- TRUE
min_blank_fold         <- 5     # keep features where mean(QC)/mean(blank) >= this

# QC CV filtering ───────────────────────────────────────────────────────────
filter_by_cv   <- TRUE
max_cv_percent <- 30

# Missing value imputation ──────────────────────────────────────────────────
impute_missing <- TRUE
imputation_method <- "missForest" # "missForest", "half_min", or "none"
fallback_impute_missing <- TRUE   # fills remaining missing/zero values after optional missForest
random_seed    <- 42       # seed for missForest imputation (ensures reproducibility)

# Log transformation ────────────────────────────────────────────────────────
do_log_transform         <- TRUE
log_base                 <- exp(1)    # exp(1) = ln, 2 = log2, 10 = log10
already_log_transformed  <- FALSE

# Statistical test ──────────────────────────────────────────────────────────
test_method          <- "t.test"    # "t.test" (Welch) or "wilcox.test"
effect_size_method   <- "hedges_g"  # "hedges_g" (pooled SD, bias-corrected) or
                                     # "glass_delta" (SD of reference group g2 only;
                                     # appropriate when Welch t-test is used and
                                     # variances between groups differ substantially)
paired_design        <- FALSE
p_adjust_method      <- "fdr"       # "fdr"/"BH", "bonferroni", "holm", "none", "SGoF"

# Significance thresholds ───────────────────────────────────────────────────
alpha       <- 0.05
fc_cutoff   <- 0.5             # log2 fold change cutoff for volcano plots
use_adj_p   <- TRUE            # use adjusted p for boxplots / significance

# Functional / enrichment analysis ──────────────────────────────────────────
do_enrichment <- TRUE          # parse lipid structural features and run category
                               # enrichment (Fisher's exact + rank-based Wilcoxon)
                               # per pairwise comparison; also exports LION inputs

# Normalization methods to run ──────────────────────────────────────────────
do_raw   <- TRUE
do_tic   <- TRUE               # TIC: divide by sum of ALL features
do_mtic  <- FALSE              # mTIC: divide by sum of curated features only
do_bmis  <- TRUE               # Best-Matched Internal Standard
do_qcrsc <- FALSE              # QC-based LOESS drift correction

# BMIS tuning ────────────────────────────────────────────────────────────────
bmis_min_cv_improvement <- 5   # minimum absolute CV reduction (%) to assign an IS;
                                # prevents noise-driven IS assignments (e.g. 25→24% CV)

# QC carryover filter ────────────────────────────────────────────────────────
filter_high_in_qc  <- FALSE    # remove features where QC signal >> sample signal
max_qc_sample_fold <- 2        # threshold: flag if median(QC) > N × median(samples)

# Volcano plot ────────────────────────────────────────────────────────────────
volcano_color_by_class <- FALSE  # TRUE: color points by lipid class (lipidomics only)
                                  # FALSE: color by Up / Down / NS direction

# Heatmap settings ──────────────────────────────────────────────────────────
heatmap_max_features <- 100    # show top-N most significant if more than this
heatmap_split_by_class <- TRUE # split large heatmaps by lipid class

# Linearity check (requires dilution-series QC samples) ──────────────────────
# TODO: set do_linearity <- TRUE and label diluted QCs as "QC_0.5x", "QC_0.25x" etc.
# in MS-DIAL before export. The pipeline will then fit intensity ~ dilution_factor
# per feature and report R² / slope. Requires at least 3 dilution points.
do_linearity <- FALSE          # not yet implemented — placeholder

# Figure format ─────────────────────────────────────────────────────────────
fig_format <- "pdf"            # "pdf" or "tiff"
fig_dpi    <- 600              # for TIFF only

# Table output format ────────────────────────────────────────────────────────
output_format <- "xlsx"         # "txt" (tab-separated, opens in Excel) or
                               # "xlsx" (requires: install.packages("writexl"))


# ══════════════════════════════════════════════════════════════════════════════
# 2. PACKAGES
# ══════════════════════════════════════════════════════════════════════════════

REQUIRED_PKGS <- c("ggplot2", "dplyr", "tidyr", "stringr", "purrr", "tibble",
                   "readr", "scales", "missForest", "pwr", "pROC",
                   "ggfortify", "RColorBrewer", "pheatmap", "patchwork")
if (p_adjust_method == "SGoF") REQUIRED_PKGS <- c(REQUIRED_PKGS, "sgof")

missing_pkgs <- REQUIRED_PKGS[!sapply(REQUIRED_PKGS, requireNamespace, quietly = TRUE)]
if (length(missing_pkgs) > 0) {
  stop("Missing packages: ", paste(missing_pkgs, collapse = ", "),
       "\nInstall with: install.packages(c('",
       paste(missing_pkgs, collapse = "', '"), "'))")
}

if (output_format == "xlsx" && !requireNamespace("writexl", quietly = TRUE)) {
  message("Note: 'writexl' not installed — falling back to txt output.",
          " Install with: install.packages('writexl')")
  output_format <- "txt"
}

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(stringr)
  library(purrr)
  library(tibble)
  library(scales)
  library(missForest)
  library(pwr)
  library(pROC)
  library(ggfortify)
  library(RColorBrewer)
  library(pheatmap)
  library(patchwork)
  if (p_adjust_method == "SGoF") library(sgof)
})


# ══════════════════════════════════════════════════════════════════════════════
# 3. EMBL-INSPIRED THEME & COLOR PALETTE
# ══════════════════════════════════════════════════════════════════════════════

# Colorblind-safe qualitative palette (Okabe-Ito + Paul Tol extensions)
CLASS_PALETTE <- c(
  "#009E73", "#0072B2", "#D55E00", "#CC79A7", "#E69F00",
  "#56B4E9", "#332288", "#44AA99", "#117733", "#999933",
  "#DDCC77", "#CC6677", "#882255", "#AA4499", "#661100",
  "#6699CC", "#888888", "#1B9E77", "#D95F02", "#7570B3"
)

# Assign consistent colors to sample classes (alphabetically sorted)
assign_class_colors <- function(classes) {
  uclass <- sort(unique(as.character(classes)))
  n <- length(uclass)
  pal <- if (n > length(CLASS_PALETTE)) {
    colorRampPalette(CLASS_PALETTE)(n)
  } else {
    CLASS_PALETTE[seq_len(n)]
  }
  setNames(pal, uclass)
}

theme_embl <- function(base_size = 11) {
  theme_minimal(base_size = base_size) %+replace%
    theme(
      text             = element_text(color = "grey15"),
      plot.title       = element_text(face = "bold", size = rel(1.15), hjust = 0,
                                      margin = margin(b = 5)),
      plot.subtitle    = element_text(color = "grey40", size = rel(0.85),
                                      margin = margin(b = 8)),
      axis.title       = element_text(face = "bold", size = rel(0.9)),
      axis.text        = element_text(color = "grey25", size = rel(0.8)),
      panel.grid.major = element_line(color = "grey92", linewidth = 0.3),
      panel.grid.minor = element_blank(),
      legend.title     = element_text(face = "bold", size = rel(0.85)),
      legend.text      = element_text(size = rel(0.8)),
      strip.text       = element_text(face = "bold", size = rel(0.9)),
      plot.margin      = margin(8, 8, 8, 8)
    )
}


# ══════════════════════════════════════════════════════════════════════════════
# 4. UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

safe_dir <- function(...) {
  p <- file.path(...)
  if (!dir.exists(p)) dir.create(p, recursive = TRUE)
  p
}

short_hash <- function(x) {
  ints <- utf8ToInt(enc2utf8(paste(x, collapse = "")))
  if (length(ints) == 0) return("00000000")
  h <- 0
  for (i in ints) h <- (h * 33 + i) %% 2147483647
  sprintf("%08x", as.integer(h))
}

safe_filename_component <- function(x, max_chars = 80) {
  clean <- gsub("[^A-Za-z0-9._-]", "_", as.character(x))
  clean <- gsub("_+", "_", clean)
  clean <- gsub("^_+|_+$", "", clean)
  if (!nzchar(clean)) clean <- "feature"
  if (nchar(clean, type = "bytes") <= max_chars) return(clean)

  hash <- short_hash(clean)
  prefix_len <- max(10, max_chars - nchar(hash, type = "bytes") - 1)
  prefix <- substr(clean, 1, prefix_len)
  prefix <- gsub("_+$", "", prefix)
  paste0(prefix, "_", hash)
}

save_fig <- function(p, filepath, w_mm = 174, h_mm = 100) {
  if (fig_format == "tiff") {
    ggsave(paste0(filepath, ".tiff"), p, width = w_mm, height = h_mm,
           units = "mm", dpi = fig_dpi, compression = "lzw")
  } else {
    ggsave(paste0(filepath, ".pdf"), p, width = w_mm / 25.4, height = h_mm / 25.4)
  }
}

save_pheatmap <- function(ph, filepath, w_mm = 174, h_mm = 200) {
  ext <- if (fig_format == "tiff") ".tiff" else ".pdf"
  if (fig_format == "tiff") {
    tiff(paste0(filepath, ext), width = w_mm, height = h_mm,
         units = "mm", res = fig_dpi, compression = "lzw")
  } else {
    pdf(paste0(filepath, ext), width = w_mm / 25.4, height = h_mm / 25.4)
  }
  grid::grid.newpage()
  grid::grid.draw(ph$gtable)
  invisible(dev.off())
}

adjust_pvalues <- function(p, method = p_adjust_method) {
  if (toupper(method) == "SGOF") {
    tryCatch({
      idx <- order(p)
      adj <- SGoF(p[idx])$Adjusted.pvalues
      adj[order(idx)]
    }, error = function(e) {
      warning("SGoF failed (", conditionMessage(e), ") — falling back to BH correction")
      p.adjust(p, method = "BH")
    })
  } else {
    p.adjust(p, method = method)
  }
}

# Safe CV: returns NA for zero-mean features
cv_pct <- function(x) {
  m <- mean(x, na.rm = TRUE)
  if (is.na(m) || m == 0) return(NA_real_)
  sd(x, na.rm = TRUE) / m * 100
}

# Save table: respects output_format — writes .txt or .xlsx
save_table <- function(x, path) {
  base <- sub("\\.[^.]+$", "", path)
  if (output_format == "xlsx") {
    writexl::write_xlsx(as.data.frame(x), paste0(base, ".xlsx"))
  } else {
    write.table(x, paste0(base, ".txt"),
                sep = "\t", quote = FALSE, row.names = FALSE, na = "")
  }
}


# ══════════════════════════════════════════════════════════════════════════════
# 4b. LIPID STRUCTURAL FEATURE PARSER & ENRICHMENT ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

# ── Parse lipid names into structural features ───────────────────────────────
# Handles MS-DIAL formats: "PC 36:2", "PC 18:1_18:0", "PC(36:2)", "PE O-36:1",
# "PE P-18:0/18:1", "SM d18:1/16:0", "Cer NS d18:1/24:0", "TG 54:3|TG 18:1…"
parse_lipid_features <- function(names) {
  n  <- gsub("\\|.*", "", as.character(names))   # first annotation only
  n  <- gsub("\\((.*)\\)$", " \\1", n)          # PC(36:2) → PC 36:2
  n  <- trimws(n)

  is_ether       <- grepl("\\bO-|EtherPC|EtherPE|LPC O|LPE O", n)
  is_plasmalogen <- grepl("\\bP-|Plas", n)
  is_lyso        <- grepl("^L(PC|PE|PI|PS|PA|PG)|^Lyso", n, ignore.case = TRUE)
  is_ether       <- is_ether & !is_plasmalogen   # O- only if not P-

  # Strip letter prefixes before digits (d18:1 → 18:1) and O-/P- markers
  n2 <- gsub("([A-Za-z])([0-9])", " \\2", gsub("[OP]-", " ", n))
  pairs <- regmatches(n2, gregexpr("\\b([0-9]+):([0-9]+)\\b", n2))

  total_C  <- sapply(pairs, function(p) if (length(p)) sum(as.integer(sub(":.*","",p))) else NA_integer_)
  total_DB <- sapply(pairs, function(p) if (length(p)) sum(as.integer(sub(".*:","",p))) else NA_integer_)

  saturation <- ifelse(is.na(total_DB), NA_character_,
                ifelse(total_DB == 0, "SFA",
                ifelse(total_DB == 1, "MUFA",
                ifelse(total_DB <= 3, "PUFA", "HUFA"))))

  data.frame(total_C = as.integer(total_C), total_DB = as.integer(total_DB),
             saturation = saturation, ether = is_ether,
             plasmalogen = is_plasmalogen, lyso = is_lyso,
             stringsAsFactors = FALSE)
}


# ── Category enrichment: Fisher's exact + rank-based Wilcoxon ────────────────
# Tests whether lipid categories (class, saturation, ether, lyso) are
# over-represented among significant features or show directional enrichment.
run_enrichment_analysis <- function(stat_df, log2fc_col, padj_col,
                                    outdir, comp_label, class_colors = NULL) {

  stat_df <- stat_df[!is.na(stat_df$Name), ]
  if (nrow(stat_df) < 5) return(invisible(NULL))

  is_sig <- !is.na(stat_df[[padj_col]]) & stat_df[[padj_col]] < alpha

  # Enrichment score for rank-based test: sign(FC) × −log10(p_adj)
  enrich_score <- sign(stat_df[[log2fc_col]]) *
    -log10(pmax(stat_df[[padj_col]], .Machine$double.xmin))

  categories <- list(
    Lipid_class = stat_df$Ontology,
    Saturation  = stat_df$saturation,
    Ether       = ifelse(stat_df$ether,       "ether",       "non-ether"),
    Plasmalogen = ifelse(stat_df$plasmalogen, "plasmalogen", "non-plasmalogen"),
    Lyso        = ifelse(stat_df$lyso,        "lyso",        "diacyl")
  )

  rows <- lapply(names(categories), function(cat_name) {
    vec <- categories[[cat_name]]
    lvls <- unique(vec[!is.na(vec)])
    do.call(rbind, lapply(lvls, function(lvl) {
      in_cat <- !is.na(vec) & vec == lvl
      if (sum(in_cat) < 3) return(NULL)
      a <- sum(is_sig & in_cat);  b <- sum(is_sig & !in_cat)
      cc <- sum(!is_sig & in_cat); d <- sum(!is_sig & !in_cat)
      ft <- tryCatch(fisher.test(matrix(c(a, cc, b, d), 2)),
                     error = function(e) NULL)
      if (is.null(ft)) return(NULL)
      es_in  <- enrich_score[in_cat  & is.finite(enrich_score)]
      es_out <- enrich_score[!in_cat & is.finite(enrich_score)]
      wt_p   <- if (length(es_in) >= 3 && length(es_out) >= 3)
        suppressWarnings(wilcox.test(es_in, es_out)$p.value) else NA_real_
      data.frame(Category = cat_name, Term = lvl,
                 n_total = sum(in_cat), n_sig = a,
                 pct_sig = round(100 * a / max(sum(in_cat), 1), 1),
                 OR      = round(unname(ft$estimate), 3),
                 OR_lo   = round(ft$conf.int[1], 3),
                 OR_hi   = round(ft$conf.int[2], 3),
                 Fisher_p       = ft$p.value,
                 median_log2FC  = round(median(stat_df[[log2fc_col]][in_cat], na.rm=TRUE), 3),
                 median_ES      = round(median(es_in, na.rm = TRUE), 3),
                 Wilcoxon_p     = wt_p,
                 stringsAsFactors = FALSE)
    }))
  })

  enrich_df <- do.call(rbind, rows)
  if (is.null(enrich_df) || nrow(enrich_df) == 0) {
    message("  Enrichment: no testable categories"); return(invisible(NULL))
  }

  enrich_df$Fisher_p_BH   <- p.adjust(enrich_df$Fisher_p,   method = "BH")
  enrich_df$Wilcoxon_p_BH <- p.adjust(enrich_df$Wilcoxon_p, method = "BH")
  enrich_df <- enrich_df[order(enrich_df$Fisher_p), ]

  save_table(enrich_df, file.path(outdir, "enrichment_analysis.txt"))

  # ── Enrichment dot plot ──────────────────────────────────────────────────
  plot_df <- enrich_df[is.finite(enrich_df$OR) & enrich_df$n_total >= 3, ]
  plot_df <- head(plot_df, 35)
  if (nrow(plot_df) > 0) {
    plot_df$Term <- factor(paste0("[", plot_df$Category, "] ", plot_df$Term),
                           levels = rev(paste0("[", plot_df$Category, "] ", plot_df$Term)))
    plot_df$adj_sig <- ifelse(!is.na(plot_df$Fisher_p_BH) & plot_df$Fisher_p_BH < 0.05, "*", "")

    p_enrich <- ggplot(plot_df,
                       aes(x = median_log2FC, y = Term,
                           size = n_sig, color = -log10(Fisher_p))) +
      geom_vline(xintercept = 0, linetype = "dashed",
                 color = "grey50", linewidth = 0.3) +
      geom_point(alpha = 0.85) +
      geom_text(aes(label = adj_sig), hjust = -0.5, size = 4.5,
                color = "#B2182B", fontface = "bold") +
      scale_color_gradient(low = "#56B4E9", high = "#B2182B",
                           name = expression(-log[10](p[Fisher]))) +
      scale_size_continuous(range = c(2, 9), name = "n significant") +
      labs(title    = paste0("Lipid Category Enrichment — ", comp_label),
           subtitle = "* BH adj. p < 0.05  |  x = median log2 FC of category",
           x = "Median log2 fold change", y = NULL) +
      theme_embl() +
      theme(legend.position = "right",
            axis.text.y = element_text(size = 7.5))
    save_fig(p_enrich, file.path(outdir, "enrichment_dotplot"),
             w_mm = 210, h_mm = max(80, nrow(plot_df) * 7 + 50))
  }

  n_sig_enrich <- sum(!is.na(enrich_df$Fisher_p_BH) & enrich_df$Fisher_p_BH < 0.05)
  message("  Enrichment: ", n_sig_enrich, " categories significant (Fisher BH adj. p < 0.05)")
  invisible(enrich_df)
}


# ── LION export: ranked list + significant list ───────────────────────────────
# LION (Lipid Ontology) web tool: https://www.lipidontology.com/
# Upload LION_ranked_all.txt for set enrichment, LION_significant.txt for ORA.
export_lion_input <- function(stat_df, log2fc_col, padj_col, outdir) {
  enrich_score <- sign(stat_df[[log2fc_col]]) *
    -log10(pmax(stat_df[[padj_col]], .Machine$double.xmin))

  # Significant list (names only)
  sig_names <- stat_df$Name[!is.na(stat_df[[padj_col]]) &
                              stat_df[[padj_col]] < alpha]
  sig_names <- sig_names[!is.na(sig_names)]
  if (length(sig_names) > 0)
    writeLines(sig_names, file.path(outdir, "LION_significant.txt"))

  # Ranked full list: Name, enrichment_score, log2FC, p_adj
  ranked <- data.frame(
    Name            = stat_df$Name,
    Enrichment_score = round(enrich_score, 4),
    log2FC          = stat_df[[log2fc_col]],
    p_adj           = stat_df[[padj_col]],
    stringsAsFactors = FALSE
  )
  ranked <- ranked[!is.na(ranked$Enrichment_score) &
                   is.finite(ranked$Enrichment_score), ]
  ranked <- ranked[order(ranked$Enrichment_score, decreasing = TRUE), ]
  save_table(ranked, file.path(outdir, "LION_ranked_all.txt"))

  message("  LION exports: ", length(sig_names), " significant + ",
          nrow(ranked), " ranked → ", basename(outdir))
}



# ══════════════════════════════════════════════════════════════════════════════
# 5. DATA LOADING & PREPROCESSING
# ══════════════════════════════════════════════════════════════════════════════

load_msdial <- function(filepath = NULL) {
  if (is.null(filepath)) filepath <- list.files(pattern = "\\.txt$")[1]
  stopifnot("No MS-DIAL .txt file found" = !is.na(filepath) && file.exists(filepath))
  message("Loading: ", filepath)

  raw <- read.delim(filepath, sep = "\t", header = FALSE, dec = ".",
                    na.strings = c("NA", ""), stringsAsFactors = FALSE)

  # Remove MS-DIAL v5 aggregate columns (Average, Stdev)
  avg_col <- which(raw[4, ] == "Average")[1]
  if (!is.na(avg_col)) raw <- raw[, 1:(avg_col - 1)]

  # Layout detection
  meta_col_end <- which(raw[1, ] != "")[1] - 1   # feature metadata columns
  n_meta_rows  <- which(raw[, 1] != "")[1] - 1    # sample info rows above header
  header_row   <- n_meta_rows + 1
  sample_start <- meta_col_end + 2                 # first sample column

  # Sample info
  sample_info <- data.frame(
    Class     = as.character(raw[1, sample_start:ncol(raw)]),
    Filetype  = as.character(raw[2, sample_start:ncol(raw)]),
    InjOrder  = as.numeric(raw[3, sample_start:ncol(raw)]),
    Name      = as.character(raw[header_row, sample_start:ncol(raw)]),
    stringsAsFactors = FALSE
  )

  # Feature info
  feat_info <- as.data.frame(raw[(header_row + 1):nrow(raw), 1:meta_col_end],
                             stringsAsFactors = FALSE)
  colnames(feat_info) <- as.character(raw[header_row, 1:meta_col_end])

  # Intensity matrix: samples (rows) x features (cols)
  int_mat <- raw[(header_row + 1):nrow(raw), sample_start:ncol(raw)]
  int_mat <- as.data.frame(lapply(int_mat, as.numeric))
  int_mat <- as.data.frame(t(int_mat))
  rownames(int_mat) <- sample_info$Name
  alignment_ids <- trimws(as.character(feat_info[["Alignment ID"]]))
  if (length(alignment_ids) != ncol(int_mat) || anyNA(alignment_ids) ||
      any(!nzchar(alignment_ids)) || anyDuplicated(alignment_ids) > 0) {
    stop("Alignment ID values must be present and unique for every feature")
  }
  colnames(int_mat) <- alignment_ids

  # Detect internal standards
  name_col <- grep("Metabolite|name", colnames(feat_info), ignore.case = TRUE)[1]
  is_idx <- if (!is.na(name_col)) grep("IS:", feat_info[[name_col]]) else integer(0)
  is_data <- is_names <- NULL
  is_number <- 0
  if (length(is_idx) > 0) {
    is_names  <- feat_info[[name_col]][is_idx]
    is_data   <- int_mat[, is_idx, drop = FALSE]
    is_number <- length(is_idx)
    # Move IS to front of data (preserves original convention)
    non_is    <- setdiff(seq_len(ncol(int_mat)), is_idx)
    int_mat   <- int_mat[, c(is_idx, non_is)]
    feat_info <- feat_info[c(is_idx, non_is), ]
  }

  # ── Multi-adduct duplicate check ──────────────────────────────────────────
  # The same lipid species may appear as multiple rows with different adducts
  # (e.g. PC 36:2 as [M+H]+ and [M+Na]+). This pipeline keeps all adduct rows
  # as separate features. Before downstream analysis, consider whether to:
  #   (a) keep the most abundant adduct per species (recommended for most studies), or
  #   (b) sum intensities across adducts — valid if the ion pool represents the same
  #       molecule and all adducts are reliably quantified.
  # Use the LipidCurator curation step or the duplicate-removal script upstream
  # to resolve multi-adduct features before running statistics.
  if (!is.na(name_col)) {
    names_nomod <- gsub("\\|.*|;.*", "", feat_info[[name_col]])
    add_col_idx <- grep("Adduct", colnames(feat_info), ignore.case = TRUE)[1]
    if (!is.na(add_col_idx)) {
      dup_key  <- paste(names_nomod, feat_info[[add_col_idx]])
      n_multi  <- sum(duplicated(names_nomod[!grepl("IS:|Unknown|RIKEN|w/o MS2",
                                                     feat_info[[name_col]])]))
      if (n_multi > 0)
        message("  Note: ", n_multi, " lipid name(s) detected in >1 adduct form.",
                " Rows are kept as separate features.",
                " Consider deduplication upstream if adduct-summing is desired.")
    }
  }

  list(raw_table = raw, intensity = int_mat, feature_info = feat_info,
       sample_info = sample_info, is_data = is_data, is_names = is_names,
       is_number = is_number, meta_col_end = meta_col_end,
       header_row = header_row, sample_start = sample_start)
}


preprocess_data <- function(dat) {
  int   <- dat$intensity
  finfo <- dat$feature_info
  sinfo <- dat$sample_info
  name_col <- grep("Metabolite|name", colnames(finfo), ignore.case = TRUE)[1]
  ont_col  <- grep("Ontology|class", colnames(finfo), ignore.case = TRUE)[1]

  # Save all-features intensity for TIC (before any feature removal)
  int_allfeatures <- int

  # --- Blank feature removal ---
  if (remove_blank_features && any(sinfo$Filetype == "Blank")) {
    # Use median (not mean) — robust against a single contaminated blank injection
    blank_med <- apply(int[sinfo$Filetype == "Blank", , drop = FALSE], 2, median, na.rm = TRUE)
    qc_med    <- apply(int[sinfo$Filetype == "QC",    , drop = FALSE], 2, median, na.rm = TRUE)
    blank_ratio <- ifelse(blank_med > 0, qc_med / blank_med, Inf)
    keep <- blank_ratio >= min_blank_fold | is.na(blank_ratio)
    n_removed_blank <- sum(!keep)
    int   <- int[, keep, drop = FALSE]
    finfo <- finfo[keep, , drop = FALSE]
    message("  Blank filter: removed ", n_removed_blank, " features")
  }

  # --- Remove blank samples ---
  non_blank <- sinfo$Filetype != "Blank"
  int_with_blank <- int  # keep for export
  int   <- int[non_blank, , drop = FALSE]
  sinfo_full <- sinfo
  sinfo <- sinfo[non_blank, , drop = FALSE]
  int_allfeatures <- int_allfeatures[non_blank, , drop = FALSE]

  # --- Remove unknowns, RIKEN, w/o MS2 ---
  if (remove_unknowns && !is.na(name_col)) {
    names_vec <- finfo[[name_col]]
    keep_feat <- !grepl("Unknown|RIKEN|w/o MS2", names_vec, ignore.case = TRUE)
    # Never remove IS
    if (dat$is_number > 0) keep_feat[1:dat$is_number] <- TRUE
    n_removed_annot <- sum(!keep_feat)
    int   <- int[, keep_feat, drop = FALSE]
    finfo <- finfo[keep_feat, , drop = FALSE]
    message("  Annotation filter: removed ", n_removed_annot,
            " Unknown/RIKEN/woMS2 features")
  }

  # --- QC carryover filter ---
  # Removes features where QC signal is disproportionately high relative to samples
  # (suggests carryover from previous injections or background contamination).
  if (filter_high_in_qc && any(sinfo$Filetype == "QC") && any(sinfo$Filetype == "Sample")) {
    qc_med     <- apply(int[sinfo$Filetype == "QC",     , drop = FALSE], 2, median, na.rm = TRUE)
    samp_med   <- apply(int[sinfo$Filetype == "Sample", , drop = FALSE], 2, median, na.rm = TRUE)
    fold_qc    <- ifelse(samp_med > 0, qc_med / samp_med, Inf)
    keep_carry <- fold_qc <= max_qc_sample_fold | is.na(fold_qc)
    if (dat$is_number > 0) keep_carry[seq_len(dat$is_number)] <- TRUE
    n_removed_carry <- sum(!keep_carry)
    int   <- int[, keep_carry, drop = FALSE]
    finfo <- finfo[keep_carry, , drop = FALSE]
    message("  QC carryover filter: removed ", n_removed_carry,
            " features (QC/sample fold > ", max_qc_sample_fold, ")")
  }

  # --- Convert zeros to NA (below LOD) ---
  int[int == 0] <- NA

  # --- Missing value summary ---
  n_miss    <- sum(is.na(int))
  pct_miss  <- round(100 * n_miss / (nrow(int) * ncol(int)), 1)
  miss_feat <- apply(int, 2, function(x) round(100 * mean(is.na(x)), 1))
  message("  Missing values: ", n_miss, " (", pct_miss, "% of matrix) | ",
          "features >50% missing: ", sum(miss_feat > 50, na.rm = TRUE))

  # --- Imputation ---
  if (impute_missing && any(is.na(int))) {
    message("  Imputing missing values with missForest (seed = ", random_seed, ") ...")

    # Warn if IS features have missing values (suggests injection failure, not true missingness)
    is_n <- dat$is_number
    if (is_n > 0 && any(is.na(int[, seq_len(is_n), drop = FALSE]))) {
      warning("Missing values in IS columns — check for injection failures in these samples: ",
              paste(rownames(int)[apply(is.na(int[, seq_len(is_n), drop = FALSE]), 1, any)],
                    collapse = ", "))
    }

    # Impute only non-IS features; IS are spike-ins at known amounts — don't impute them
    non_is_cols <- if (is_n > 0) seq(is_n + 1, ncol(int)) else seq_len(ncol(int))
    if (length(non_is_cols) > 0 && any(is.na(int[, non_is_cols, drop = FALSE]))) {
      set.seed(random_seed)
      imp_res <- missForest(int[, non_is_cols, drop = FALSE], verbose = FALSE)
      int[, non_is_cols] <- imp_res$ximp
      message("  Imputation OOB error (NRMSE): ", round(imp_res$OOBerror["NRMSE"], 3))
    }
    message("  Imputation complete")
  }

  if (fallback_impute_missing) {
    # Minimum fallback for any remaining NA/zero values.
    # The default keeps the historical min x random behavior; modular wrappers
    # can request a deterministic half-min fallback through imputation_method.
    set.seed(random_seed)
    for (j in seq_len(ncol(int))) {
      bad <- is.na(int[, j]) | int[, j] <= 0
      if (any(bad)) {
        pos_vals <- int[, j][!bad & is.finite(int[, j])]
        min_val  <- if (length(pos_vals) > 0) min(pos_vals) else 1
        if (identical(imputation_method, "half_min")) {
          int[bad, j] <- min_val * 0.5
        } else {
          int[bad, j] <- min_val * runif(sum(bad), 0.001, 0.999)
        }
      }
    }
  }

  list(intensity = int, intensity_allfeatures = int_allfeatures,
       feature_info = finfo, sample_info = sinfo, sample_info_full = sinfo_full,
       is_data = if (dat$is_number > 0) int[, 1:dat$is_number, drop = FALSE] else NULL,
       is_names = dat$is_names, is_number = dat$is_number,
       raw_table = dat$raw_table, meta_col_end = dat$meta_col_end,
       header_row = dat$header_row, sample_start = dat$sample_start)
}


# ══════════════════════════════════════════════════════════════════════════════
# 6. NORMALIZATION FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

normalize_tic <- function(int, int_allfeatures) {
  # TIC: divide by total ion current of ALL features (including removed ones)
  tic <- rowSums(int_allfeatures, na.rm = TRUE)
  tic[tic == 0] <- 1
  sweep(int, 1, tic, "/")
}

normalize_mtic <- function(int, is_number = 0) {
  # mTIC: sum of endogenous (non-IS) curated features only.
  # IS are spike-ins added at known amounts — including them biases the denominator.
  endo_cols <- if (is_number > 0) seq(is_number + 1, ncol(int)) else seq_len(ncol(int))
  tic <- rowSums(int[, endo_cols, drop = FALSE], na.rm = TRUE)
  tic[tic == 0] <- 1
  sweep(int, 1, tic, "/")
}

normalize_bmis <- function(int, filetype, is_data, is_names,
                           min_cv_improvement = bmis_min_cv_improvement) {
  if (is.null(is_data) || ncol(is_data) == 0) {
    warning("No internal standards found — skipping BMIS normalization")
    return(list(data = int, assignment = NULL))
  }

  n_is <- ncol(is_data)
  n_feat <- ncol(int)
  qc_idx <- which(filetype == "QC")
  best_is_name  <- character(n_feat)
  raw_cv_val    <- numeric(n_feat)
  best_cv_val   <- numeric(n_feat)
  normalized    <- int

  for (j in seq_len(n_feat)) {
    # CV without normalization
    qc_raw <- int[qc_idx, j]
    cvs <- cv_pct(qc_raw)
    raw_cv_val[j] <- if (is.na(cvs)) Inf else cvs

    best_k   <- 0
    best_cv  <- cvs  # candidate must beat raw CV by >= min_cv_improvement

    for (k in seq_len(n_is)) {
      norm_vals <- int[, j] / is_data[, k]
      qc_cv <- cv_pct(norm_vals[qc_idx])
      # Assign only if improvement exceeds the minimum threshold
      if (!is.na(qc_cv) && (is.na(best_cv) || qc_cv < best_cv - min_cv_improvement)) {
        best_cv <- qc_cv
        best_k  <- k
      }
    }

    best_cv_val[j] <- if (best_k > 0) best_cv else raw_cv_val[j]
    if (best_k == 0) {
      best_is_name[j] <- "none"
    } else {
      best_is_name[j] <- is_names[best_k]
      normalized[, j] <- int[, j] / is_data[, best_k]
    }
  }

  assignment <- data.frame(
    Feature       = colnames(int),
    Best_IS       = best_is_name,
    Raw_CV_pct    = round(raw_cv_val, 2),
    Min_CV_pct    = round(best_cv_val, 2),
    CV_improvement = round(raw_cv_val - best_cv_val, 2),
    stringsAsFactors = FALSE
  )
  # Cap Inf placeholders for readability
  assignment$Raw_CV_pct[!is.finite(assignment$Raw_CV_pct)] <- NA

  list(data = normalized, assignment = assignment)
}

normalize_qcrsc <- function(int, filetype, injection_order,
                            outdir = NULL, n_audit = 6) {
  # QC-based Robust LOESS Signal Correction (QC-RSC)
  # outdir: if provided, saves audit plots for the top-n_audit highest-drift features
  qc_idx    <- which(filetype == "QC")
  qc_orders <- injection_order[qc_idx]
  corrected <- int
  n_qc      <- length(qc_idx)
  n_feat    <- ncol(int)

  # Adaptive span: wider when QC points are sparse to avoid overfitting.
  # With <4 QCs the LOESS fit is unreliable; fall back to linear interpolation.
  loess_span <- if (n_qc >= 12) 0.75
                else if (n_qc >= 8) 0.85
                else 1.0   # full span ≈ global linear trend, safest for small n
  # degree = 2 (locally quadratic) captures curvature in drift patterns;
  # fall back to degree = 1 when span = 1 (global fit, too few QCs for curvature)
  loess_degree <- if (loess_span < 1.0) 2L else 1L

  if (n_qc < 4) {
    warning("Only ", n_qc, " QC sample(s) — QC-RSC is unreliable with fewer than 4 QCs. ",
            "Falling back to linear interpolation for all features.")
  } else if (n_qc < 8) {
    message("  QC-RSC: ", n_qc, " QC samples, using LOESS span = ", loess_span)
  }

  # Store LOESS-predicted values at QC positions for drift audit
  pred_at_qc <- matrix(NA_real_, nrow = n_qc, ncol = n_feat)

  for (j in seq_len(n_feat)) {
    qc_vals <- int[qc_idx, j]
    med_qc  <- median(qc_vals, na.rm = TRUE)

    if (n_qc >= 4) {
      lo <- tryCatch(
        loess(qc_vals ~ qc_orders, span = loess_span, degree = loess_degree),
        error = function(e) NULL
      )
      if (!is.null(lo)) {
        predicted <- predict(lo, newdata = data.frame(qc_orders = injection_order))
      } else {
        predicted <- approx(qc_orders, qc_vals, xout = injection_order, rule = 2)$y
      }
    } else {
      predicted <- approx(qc_orders, qc_vals, xout = injection_order, rule = 2)$y
    }

    pred_at_qc[, j] <- predicted[qc_idx]
    correction <- med_qc / predicted
    correction[!is.finite(correction) | correction <= 0] <- 1
    corrected[, j] <- int[, j] * correction
  }

  # ── Audit plots for highest-drift features ──────────────────────────────────
  if (!is.null(outdir) && n_qc >= 4) {
    # Drift amplitude: (max - min) of LOESS fit / median, expressed as %
    drift_pct <- apply(pred_at_qc, 2, function(x) {
      r <- range(x, na.rm = TRUE); m <- median(x, na.rm = TRUE)
      if (is.na(m) || m == 0) return(0)
      (r[2] - r[1]) / m * 100
    })

    # Summary table (all features, ranked by drift)
    drift_tbl <- data.frame(
      Feature   = colnames(int),
      Drift_pct = round(drift_pct, 2),
      stringsAsFactors = FALSE
    )[order(drift_pct, decreasing = TRUE), ]
    save_table(drift_tbl, file.path(outdir, "QC-RSC_drift_summary.txt"))

    # Individual correction plots for top-N
    top_idx <- order(drift_pct, decreasing = TRUE)[seq_len(min(n_audit, n_feat))]
    feat_labels <- colnames(int)

    plot_list <- lapply(top_idx, function(j) {
      df <- data.frame(
        Order     = qc_orders,
        Raw       = as.numeric(int[qc_idx, j]),
        LOESS_fit = pred_at_qc[, j],
        Corrected = as.numeric(corrected[qc_idx, j]),
        stringsAsFactors = FALSE
      )
      df_long <- tidyr::pivot_longer(df, c(Raw, LOESS_fit, Corrected),
                                     names_to = "Type", values_to = "Value")
      df_long$Type <- factor(df_long$Type, levels = c("Raw", "LOESS_fit", "Corrected"))

      ggplot2::ggplot(df_long, ggplot2::aes(x = Order, y = Value, color = Type)) +
        ggplot2::geom_line(linewidth = 0.6) +
        ggplot2::geom_point(size = 1.8) +
        ggplot2::scale_color_manual(
          values = c(Raw = "#E69F00", LOESS_fit = "#0072B2", Corrected = "#009E73"),
          labels = c(Raw = "Raw QC", LOESS_fit = "LOESS fit", Corrected = "Corrected QC")
        ) +
        ggplot2::labs(
          title    = feat_labels[j],
          subtitle = sprintf("Drift amplitude: %.1f%%", drift_pct[j]),
          x = "Injection order", y = "Intensity", color = NULL
        ) +
        theme_embl() +
        ggplot2::theme(legend.position = "bottom")
    })

    n_rows <- ceiling(length(plot_list) / 2)
    combined <- patchwork::wrap_plots(plot_list, ncol = 2) +
      patchwork::plot_annotation(
        title = sprintf("QC-RSC Audit — Top %d Drifting Features (span=%.2f)",
                        length(plot_list), loess_span),
        theme = ggplot2::theme(plot.title = ggplot2::element_text(face = "bold"))
      )
    save_fig(combined, file.path(outdir, "QC-RSC_top_drift"),
             w_mm = 280, h_mm = n_rows * 90 + 40)
    message("  QC-RSC audit: top-", length(plot_list), " drift features saved to: ", outdir)
  }

  corrected
}


# ══════════════════════════════════════════════════════════════════════════════
# 7. PLOTTING FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

# ── QC Precision ─────────────────────────────────────────────────────────────

plot_qc_precision <- function(cv_vec, title = "QC Precision", colors = NULL) {
  df <- data.frame(CV = cv_vec)
  p <- ggplot(df, aes(x = "", y = CV)) +
    geom_boxplot(width = 0.5, fill = "#56B4E9", alpha = 0.6, outlier.size = 0.8) +
    geom_jitter(width = 0.12, size = 0.6, alpha = 0.4, color = "grey30") +
    geom_hline(yintercept = 30, linetype = "dashed", color = "#D55E00", linewidth = 0.4) +
    geom_hline(yintercept = 20, linetype = "dashed", color = "#009E73", linewidth = 0.4) +
    labs(title = title,
         subtitle = paste0("median: ", round(median(cv_vec, na.rm = TRUE), 1),
                           "% | n = ", sum(!is.na(cv_vec)), " features"),
         y = "CV [%]", x = NULL) +
    theme_embl() +
    theme(axis.text.x = element_blank())
  p
}

plot_qc_histogram <- function(cv_vec, title = "QC CV Distribution") {
  df <- data.frame(CV = cv_vec)
  p <- ggplot(df, aes(x = CV)) +
    geom_histogram(binwidth = 2, fill = "#56B4E9", color = "white", alpha = 0.8) +
    geom_vline(xintercept = 30, linetype = "dashed", color = "#D55E00") +
    geom_vline(xintercept = 20, linetype = "dashed", color = "#009E73") +
    labs(title = title, x = "CV [%]", y = "Count") +
    theme_embl()
  p
}


# ── RLA Plots ────────────────────────────────────────────────────────────────

plot_rla <- function(data_log, class_vec, type = c("wg", "ag"), colors = NULL,
                     title_suffix = "") {
  type <- match.arg(type)
  type_label <- if (type == "wg") "Within-group" else "Across-group"

  rla <- as.matrix(data_log)
  if (type == "ag") {
    ref <- apply(rla, 2, median, na.rm = TRUE)
    rla <- sweep(rla, 2, ref)
  } else {
    for (g in unique(class_vec)) {
      idx <- which(class_vec == g)
      ref <- apply(rla[idx, , drop = FALSE], 2, median, na.rm = TRUE)
      rla[idx, ] <- sweep(rla[idx, , drop = FALSE], 2, ref)
    }
  }

  rla_long <- data.frame(
    Sample = rep(rownames(data_log), ncol(data_log)),
    Class  = rep(class_vec, ncol(data_log)),
    RLA    = as.vector(rla),
    stringsAsFactors = FALSE
  )
  rla_long$Sample <- factor(rla_long$Sample, levels = rownames(data_log))

  # Compute per-sample outlier rate (IQR × 1.5 rule) for subtitle annotation
  outlier_pcts <- tapply(rla_long$RLA, rla_long$Sample, function(x) {
    x <- x[is.finite(x)]
    if (length(x) < 4) return(NA_real_)
    q   <- quantile(x, c(0.25, 0.75))
    iqr <- q[2] - q[1]
    mean(x < q[1] - 1.5 * iqr | x > q[2] + 1.5 * iqr) * 100
  })
  med_out <- round(median(outlier_pcts, na.rm = TRUE), 1)
  max_out <- round(max(outlier_pcts,    na.rm = TRUE), 1)
  sub_txt <- sprintf("Outliers (>1.5\u00d7IQR) hidden — per-sample rate: median %.1f%%, max %.1f%%",
                     med_out, max_out)

  p <- ggplot(rla_long, aes(x = Sample, y = RLA, fill = Class)) +
    geom_boxplot(outlier.shape = NA, linewidth = 0.3, width = 0.7) +
    geom_hline(yintercept = 0, linetype = "dashed", color = "grey50", linewidth = 0.4) +
    coord_cartesian(ylim = c(-2, 2)) +
    labs(title = paste0(type_label, " RLA Plot", title_suffix),
         subtitle = sub_txt,
         y = "Relative Log Abundance", x = NULL) +
    theme_embl() +
    theme(axis.text.x = element_text(angle = 90, hjust = 1, vjust = 0.5, size = 5),
          plot.subtitle = element_text(size = rel(0.75), color = "grey45"))

  if (!is.null(colors)) p <- p + scale_fill_manual(values = colors)
  p
}


# ── PCA ──────────────────────────────────────────────────────────────────────

plot_pca <- function(data_mat, class_vec, colors, title = "PCA",
                     scale_data = FALSE, label = TRUE) {
  mat <- as.matrix(data_mat)
  finite_cols <- apply(mat, 2, function(x) all(is.finite(x)))
  mat <- mat[, finite_cols, drop = FALSE]
  row_keep <- stats::complete.cases(mat)
  mat <- mat[row_keep, , drop = FALSE]
  class_vec <- class_vec[row_keep]

  if (scale_data && ncol(mat) > 0) {
    varying_cols <- apply(mat, 2, function(x) stats::sd(x, na.rm = TRUE) > 0)
    mat <- mat[, varying_cols, drop = FALSE]
  }

  if (nrow(mat) < 2 || ncol(mat) < 2) {
    return(
      ggplot() +
        annotate("text", x = 0, y = 0, label = "PCA skipped: too few variable features",
                 size = 3, color = "grey35") +
        labs(title = title) +
        theme_void()
    )
  }

  pca_res <- prcomp(mat, scale. = scale_data)
  df <- data.frame(Class = class_vec)

  p <- autoplot(pca_res, data = df, colour = "Class",
                frame = TRUE, frame.type = "t",
                label = label, label.size = 2.5) +
    scale_color_manual(values = colors) +
    scale_fill_manual(values = alpha(colors, 0.2)) +
    labs(title = title) +
    theme_embl()
  p
}


# ── Volcano ──────────────────────────────────────────────────────────────────

plot_volcano <- function(log2fc, pvals, title = NULL, p_thresh = alpha,
                         fc_thresh = fc_cutoff, use_adj = FALSE,
                         class_vec = NULL) {
  log2fc[is.na(log2fc) | !is.finite(log2fc)] <- 0
  pvals[is.na(pvals)] <- 1
  pvals[pvals == 0]   <- .Machine$double.xmin

  df <- data.frame(FC = log2fc, P = pvals)
  df$neglog10p <- -log10(df$P)
  df$Direction <- case_when(
    df$FC >  fc_thresh & df$P < p_thresh ~ "Up",
    df$FC < -fc_thresh & df$P < p_thresh ~ "Down",
    TRUE ~ "NS"
  )
  df$significant <- df$Direction != "NS"

  use_class <- volcano_color_by_class && !is.null(class_vec) &&
               length(class_vec) == nrow(df)

  if (use_class) {
    df$Class <- class_vec
    pal <- assign_class_colors(class_vec)
    p <- ggplot(df, aes(x = FC, y = neglog10p, color = Class,
                        alpha = significant)) +
      geom_point(size = 0.9) +
      scale_color_manual(values = pal, name = "Lipid class") +
      scale_alpha_manual(values = c("FALSE" = 0.25, "TRUE" = 0.85), guide = "none")
  } else {
    p <- ggplot(df, aes(x = FC, y = neglog10p, color = Direction)) +
      geom_point(size = 0.9, alpha = 0.6) +
      scale_color_manual(values = c(Down = "#2166AC", NS = "grey75", Up = "#B2182B"),
                         guide = "none")
  }

  p <- p +
    geom_hline(yintercept = -log10(p_thresh), linetype = "dashed",
               color = "grey50", linewidth = 0.3) +
    geom_vline(xintercept = c(-fc_thresh, fc_thresh), linetype = "dashed",
               color = "grey50", linewidth = 0.3) +
    labs(x = expression(log[2]~"fold change"),
         y = expression(-log[10]~italic(p)~"value"),
         title = title,
         subtitle = paste0("Up: ", sum(df$Direction == "Up"),
                           " | Down: ", sum(df$Direction == "Down"))) +
    theme_embl()
  p
}


# ── Lipid-class fold-change boxplot ──────────────────────────────────────────

plot_class_fc <- function(fc_vec, class_vec, pvals = NULL, p_thresh = alpha,
                          title = "Fold Change by Lipid Class",
                          subtitle = "") {
  df <- data.frame(FC = log2(fc_vec), LipidClass = class_vec,
                   stringsAsFactors = FALSE)
  df <- df[is.finite(df$FC), ]

  # Order classes by median FC
  ord <- df %>% group_by(LipidClass) %>%
    summarise(med = median(FC, na.rm = TRUE), .groups = "drop") %>%
    arrange(med) %>% pull(LipidClass)
  df$LipidClass <- factor(df$LipidClass, levels = ord)

  n_cls <- length(ord)
  pal <- if (n_cls > length(CLASS_PALETTE)) {
    colorRampPalette(CLASS_PALETTE)(n_cls)
  } else {
    CLASS_PALETTE[seq_len(n_cls)]
  }
  color_map <- setNames(pal, ord)

  p <- ggplot(df, aes(x = LipidClass, y = FC, fill = LipidClass)) +
    geom_hline(yintercept = 0, linetype = "dashed", color = "grey50", linewidth = 0.3) +
    geom_boxplot(width = 0.6, alpha = 0.8, outlier.size = 0.7, outlier.alpha = 0.4,
                 color = "grey30", linewidth = 0.3) +
    geom_jitter(width = 0.12, size = 0.5, alpha = 0.3, color = "grey20") +
    scale_fill_manual(values = color_map, guide = "none") +
    labs(title = title, subtitle = subtitle, x = NULL, y = "log2 Fold Change") +
    theme_embl() +
    theme(axis.text.x = element_text(angle = 45, hjust = 1, size = 8),
          panel.grid.major.x = element_blank())
  p
}


# ── Heatmap (pheatmap wrapper) ───────────────────────────────────────────────

make_heatmap <- function(data_mat, sample_classes, feature_names = NULL,
                         title = "Heatmap", filepath = NULL,
                         cluster_cols = TRUE, max_features = heatmap_max_features,
                         class_colors = NULL, sort_by_group = FALSE) {
  if (nrow(data_mat) < 2 || ncol(data_mat) < 2) {
    message("  Skipping heatmap: too few features or samples")
    return(invisible(NULL))
  }

  # Limit features for readability
  if (ncol(data_mat) > max_features) {
    # Keep top features by variance
    feat_var <- apply(data_mat, 2, var, na.rm = TRUE)
    top_idx  <- order(feat_var, decreasing = TRUE)[seq_len(max_features)]
    data_mat <- data_mat[, top_idx, drop = FALSE]
    if (!is.null(feature_names)) feature_names <- feature_names[top_idx]
    title <- paste0(title, " (top ", max_features, " by variance)")
  }

  # Sort samples by group for supervised heatmap (cluster_cols=FALSE)
  if (sort_by_group || !cluster_cols) {
    ord       <- order(sample_classes)
    data_mat  <- data_mat[ord, , drop = FALSE]
    sample_classes <- sample_classes[ord]
  }

  if (!is.null(feature_names)) colnames(data_mat) <- feature_names

  # Annotation
  ann_col <- data.frame(Class = sample_classes)
  rownames(ann_col) <- rownames(data_mat)

  ann_colors <- list()
  if (!is.null(class_colors)) {
    ann_colors$Class <- class_colors[names(class_colors) %in% unique(sample_classes)]
  }

  # Scale rows (features) for z-score heatmap
  heat_mat <- scale(t(data_mat))  # features × samples, z-scored per feature

  # Font size scales with feature count
  fontsize_row <- max(3, min(8, 200 / nrow(heat_mat)))

  n_groups <- length(unique(sample_classes))
  ph <- pheatmap(heat_mat,
                 color = rev(colorRampPalette(brewer.pal(10, "RdBu"))(100)),
                 annotation_col = ann_col,
                 annotation_colors = ann_colors,
                 cluster_cols = cluster_cols,
                 clustering_distance_rows = "manhattan",
                 clustering_distance_cols = "manhattan",
                 cutree_cols = if (cluster_cols) min(n_groups, 6L) else NA,
                 show_colnames = TRUE,
                 fontsize_row = fontsize_row,
                 fontsize_col = 6,
                 main = title,
                 silent = TRUE)

  if (!is.null(filepath)) {
    h_mm <- max(100, min(400, nrow(heat_mat) * 3.5 + 60))
    save_pheatmap(ph, filepath, w_mm = 250, h_mm = h_mm)
  }

  invisible(ph)
}


# ── Paired boxplot for significant features ──────────────────────────────────

plot_feature_boxplot <- function(values, class_vec, feat_name, stats_text,
                                 colors, paired = FALSE, pairs = NULL) {
  df <- data.frame(Value = values, Class = class_vec)

  p <- ggplot(df, aes(x = Class, y = Value, fill = Class)) +
    geom_boxplot(width = 0.5, alpha = 0.7, outlier.shape = NA) +
    geom_jitter(width = 0.1, size = 1.2, alpha = 0.5) +
    scale_fill_manual(values = colors, guide = "none") +
    labs(title = feat_name, subtitle = stats_text, y = "Intensity", x = NULL) +
    theme_embl()

  if (paired && !is.null(pairs)) {
    df$Pair <- pairs
    p <- p + geom_line(aes(group = Pair), data = df,
                       linetype = 2, color = "grey60", linewidth = 0.3)
  }
  p
}


# ── Injection-order drift ─────────────────────────────────────────────────────

plot_injection_order_drift <- function(data_mat, filetype, injection_order, class_vec,
                                       title_suffix = "") {
  tic   <- rowSums(data_mat, na.rm = TRUE)
  is_qc <- filetype == "QC"
  df    <- data.frame(
    TIC      = tic,
    InjOrder = injection_order,
    Class    = ifelse(is_qc, "QC", class_vec),
    IsQC     = is_qc,
    stringsAsFactors = FALSE
  )
  df <- df[order(df$InjOrder), ]

  p <- ggplot(df, aes(x = InjOrder, y = log10(TIC + 1))) +
    geom_point(aes(color = Class, shape = IsQC), size = 2.2, alpha = 0.85) +
    geom_smooth(data = df[!df$IsQC, ], method = "loess", formula = y ~ x,
                se = TRUE, color = "grey30", fill = "grey80",
                linewidth = 0.5, linetype = "solid") +
    scale_shape_manual(values = c("FALSE" = 16, "TRUE" = 8),
                       labels = c("Sample", "QC"), name = "Type") +
    labs(title = paste0("Injection-Order TIC Drift", title_suffix),
         subtitle = "LOESS trend on biological samples; QC injections marked (*)",
         x = "Injection order", y = "log\u2081\u2080(TIC)", color = "Class") +
    theme_embl()
  p
}


# ── Sample outlier detection (PCA-based Mahalanobis distance) ─────────────────

detect_sample_outliers <- function(data_mat, class_vec, filetype,
                                   n_pc = 5, alpha_thresh = 0.01) {
  # Use only sample rows; complete features only
  sample_mask <- filetype == "Sample"
  mat <- data_mat[sample_mask, , drop = FALSE]
  cls <- class_vec[sample_mask]
  mat_cc <- mat[, apply(mat, 2, function(x) all(is.finite(x))), drop = FALSE]
  if (ncol(mat_cc) > 0) {
    varying_cols <- apply(mat_cc, 2, function(x) stats::sd(x, na.rm = TRUE) > 0)
    mat_cc <- mat_cc[, varying_cols, drop = FALSE]
  }
  if (ncol(mat_cc) < 2 || nrow(mat_cc) < 3) return(NULL)

  pca_res <- prcomp(mat_cc, scale. = TRUE)
  n_pc    <- min(n_pc, ncol(pca_res$x), nrow(pca_res$x) - 1)
  scores  <- pca_res$x[, seq_len(n_pc), drop = FALSE]

  cov_mat  <- tryCatch(cov(scores), error = function(e) diag(n_pc))
  mah_d2   <- tryCatch(
    mahalanobis(scores, colMeans(scores), cov_mat),
    error = function(e) rowSums(scale(scores)^2)
  )
  threshold <- qchisq(1 - alpha_thresh, df = n_pc)

  result <- data.frame(
    Sample       = rownames(mat),
    Class        = cls,
    Mahalanobis_D2 = round(mah_d2, 2),
    Chi2_threshold = round(threshold, 2),
    Outlier      = mah_d2 > threshold,
    stringsAsFactors = FALSE
  )

  # Plot: PCA scores coloured by class, outliers labelled
  df_plot <- data.frame(
    PC1     = pca_res$x[, 1],
    PC2     = pca_res$x[, 2],
    Class   = cls,
    D2      = mah_d2,
    Outlier = mah_d2 > threshold,
    Label   = ifelse(mah_d2 > threshold, rownames(mat), ""),
    stringsAsFactors = FALSE
  )
  var_exp <- round(100 * pca_res$sdev^2 / sum(pca_res$sdev^2), 1)
  colors  <- assign_class_colors(cls)

  p_out <- ggplot(df_plot, aes(x = PC1, y = PC2, color = Class)) +
    geom_point(aes(size = D2, shape = Outlier), alpha = 0.8) +
    ggrepel_label(df_plot) +
    scale_shape_manual(values = c("FALSE" = 16, "TRUE" = 4), guide = "none") +
    scale_size_continuous(range = c(1.5, 5), guide = "none") +
    scale_color_manual(values = colors) +
    labs(title = "Sample Outlier Detection (PCA + Mahalanobis D²)",
         subtitle = sprintf("Chi\u00B2 threshold (df=%d, \u03B1=%.2f): %.1f  |  Outliers: %d",
                            n_pc, alpha_thresh, threshold, sum(mah_d2 > threshold)),
         x = sprintf("PC1 (%.1f%%)", var_exp[1]),
         y = sprintf("PC2 (%.1f%%)", var_exp[2])) +
    theme_embl()

  list(table = result, plot = p_out)
}

# Lightweight ggrepel-style label fallback (avoids hard dependency on ggrepel)
ggrepel_label <- function(df) {
  if (requireNamespace("ggrepel", quietly = TRUE)) {
    ggrepel::geom_text_repel(
      data = df[df$Outlier, , drop = FALSE],
      aes(label = Label), size = 2.8, color = "grey20",
      box.padding = 0.4, max.overlaps = 20
    )
  } else {
    ggplot2::geom_text(
      data = df[df$Outlier, , drop = FALSE],
      aes(label = Label), size = 2.5, vjust = -0.8, color = "grey20"
    )
  }
}


# ── PCA loadings export ───────────────────────────────────────────────────────

save_pca_loadings <- function(data_mat, feat_names, outdir, title_suffix = "",
                               n_top = 15) {
  finite_cols <- apply(data_mat, 2, function(x) all(is.finite(x)))
  mat_cc <- data_mat[, finite_cols, drop = FALSE]
  if (ncol(mat_cc) > 0) {
    varying_cols <- apply(mat_cc, 2, function(x) stats::sd(x, na.rm = TRUE) > 0)
    mat_cc <- mat_cc[, varying_cols, drop = FALSE]
    finite_cols[finite_cols] <- varying_cols
  }
  if (ncol(mat_cc) < 2) return(invisible(NULL))

  pca_res  <- prcomp(mat_cc, scale. = FALSE)
  var_exp  <- round(100 * pca_res$sdev^2 / sum(pca_res$sdev^2), 2)
  loadings <- as.data.frame(pca_res$rotation[, 1:min(5, ncol(pca_res$rotation))])
  # Map back to feature names (mat_cc may be a column subset)
  load_feat_names <- if (!is.null(feat_names) && length(feat_names) == ncol(data_mat)) {
    feat_names[finite_cols]
  } else if (!is.null(feat_names) && length(feat_names) == ncol(mat_cc)) {
    feat_names
  } else {
    colnames(mat_cc)
  }
  loadings$Feature <- load_feat_names
  save_table(loadings, file.path(outdir, "PCA_loadings.txt"))

  # Barplot: top N contributors for PC1 and PC2
  plot_list <- list()
  for (pc in c("PC1", "PC2")) {
    if (!pc %in% colnames(loadings)) next
    top <- loadings[order(abs(loadings[[pc]]), decreasing = TRUE)[seq_len(min(n_top, nrow(loadings)))], ]
    top <- top[order(top[[pc]]), , drop = FALSE]
    top$Feature_label <- factor(make.unique(as.character(top$Feature), sep = " #"),
                                levels = make.unique(as.character(top$Feature), sep = " #"))
    pv <- ggplot(top, aes(x = Feature_label, y = .data[[pc]],
                          fill = .data[[pc]] > 0)) +
      geom_col(alpha = 0.85) +
      coord_flip() +
      scale_fill_manual(values = c("TRUE" = "#B2182B", "FALSE" = "#2166AC"),
                        guide = "none") +
      geom_hline(yintercept = 0, linewidth = 0.3, color = "grey40") +
      labs(title = paste0(pc, " Loadings — top ", n_top, title_suffix),
           subtitle = sprintf("Variance explained: %.1f%%", var_exp[as.integer(sub("PC","",pc))]),
           x = NULL, y = "Loading") +
      theme_embl() +
      theme(axis.text.y = element_text(size = 6))
    save_fig(pv, file.path(outdir, paste0("PCA_loadings_", pc)),
             w_mm = 120, h_mm = max(80, n_top * 5 + 30))
    plot_list[[pc]] <- pv
  }
  invisible(plot_list)
}


# ══════════════════════════════════════════════════════════════════════════════
# 8. STATISTICAL ANALYSIS FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

run_anova <- function(data_mat, class_vec, feat_info, outdir, class_colors) {
  message("  Running ANOVA ...")
  n_feat  <- ncol(data_mat)
  p_anova <- numeric(n_feat)
  f_stat  <- numeric(n_feat)
  eta_sq  <- numeric(n_feat)

  for (j in seq_len(n_feat)) {
    tryCatch({
      # Welch's one-way ANOVA (doesn't assume equal variances)
      wt <- oneway.test(data_mat[, j] ~ class_vec)
      p_anova[j] <- wt$p.value
      f_stat[j]  <- wt$statistic

      # eta-squared from classical ANOVA (slightly biased but widely reported)
      mod  <- aov(data_mat[, j] ~ class_vec)
      ss   <- summary(mod)[[1]]$`Sum Sq`
      eta_sq[j] <- ss[1] / sum(ss)
    }, error = function(e) {
      p_anova[j] <<- NA_real_
      f_stat[j]  <<- NA_real_
      eta_sq[j]  <<- NA_real_
    })
  }

  p_adj <- adjust_pvalues(p_anova)

  name_col   <- grep("Metabolite|name", colnames(feat_info), ignore.case = TRUE)[1]
  feat_names <- if (!is.na(name_col)) feat_info[[name_col]] else colnames(data_mat)

  # ── Save ANOVA results table ──
  anova_tbl <- data.frame(
    Feature     = feat_names,
    F_statistic = round(f_stat, 3),
    p_value     = p_anova,
    p_adj       = p_adj,
    Eta_squared = round(eta_sq, 4),
    stringsAsFactors = FALSE
  )
  colnames(anova_tbl)[4] <- paste0("p_adj_", p_adjust_method)
  save_table(anova_tbl, file.path(outdir, "ANOVA_results.txt"))
  message("  ANOVA: ", sum(!is.na(p_anova) & p_anova < alpha), " raw-significant / ",
          sum(!is.na(p_adj) & p_adj < alpha), " adj-significant features")

  # ── Heatmaps for significant features ──
  for (ptype in c("raw", "adj")) {
    sig <- which(if (ptype == "raw") p_anova < alpha else p_adj < alpha)
    if (length(sig) > 1) {
      label <- if (ptype == "raw") "ANOVA p < 0.05" else "ANOVA adj.p < 0.05"
      make_heatmap(data_mat[, sig, drop = FALSE], class_vec,
                   feature_names = feat_names[sig],
                   title = label,
                   filepath = file.path(outdir, paste0("Heatmap_ANOVA_", ptype)),
                   class_colors = class_colors)
      make_heatmap(data_mat[, sig, drop = FALSE], class_vec,
                   feature_names = feat_names[sig],
                   title = paste(label, "(supervised)"),
                   filepath = file.path(outdir, paste0("Heatmap_ANOVA_", ptype, "_supervised")),
                   cluster_cols = FALSE, class_colors = class_colors)
    }
  }

  list(p = p_anova, p_adj = p_adj, f = f_stat, eta_sq = eta_sq)
}


run_pairwise <- function(data_mat, class_vec, filetype, feat_info,
                         outdir, class_colors, precQC = NULL) {
  samples_only <- filetype != "QC"
  data_samples <- data_mat[samples_only, , drop = FALSE]
  class_samples <- class_vec[samples_only]
  class_levels <- sort(unique(class_samples))
  comps <- combn(class_levels, 2)
  n_feat <- ncol(data_mat)

  name_col <- grep("Metabolite|name", colnames(feat_info), ignore.case = TRUE)[1]
  ont_col  <- grep("Ontology|class", colnames(feat_info), ignore.case = TRUE)[1]
  rt_col   <- grep("^RT|retention", colnames(feat_info), ignore.case = TRUE)[1]
  mz_col   <- grep("^m/z|^mz|mass", colnames(feat_info), ignore.case = TRUE)[1]
  add_col  <- grep("Adduct", colnames(feat_info), ignore.case = TRUE)[1]
  feat_names <- if (!is.na(name_col)) feat_info[[name_col]] else colnames(data_mat)

  stat_fn <- if (test_method == "wilcox.test") wilcox.test else t.test

  for (ci in seq_len(ncol(comps))) {
    g1 <- comps[1, ci]; g2 <- comps[2, ci]
    comp_label <- paste(g1, "vs", g2)
    message("  Comparison: ", comp_label)
    comp_dir <- safe_dir(outdir, paste(g1, "vs", g2))

    idx1 <- which(class_samples == g1)
    idx2 <- which(class_samples == g2)
    n1   <- length(idx1); n2 <- length(idx2)
    sub_data  <- data_samples[c(idx1, idx2), , drop = FALSE]
    sub_class <- class_samples[c(idx1, idx2)]
    d1 <- sub_data[seq_along(idx1), , drop = FALSE]
    d2 <- sub_data[length(idx1) + seq_along(idx2), , drop = FALSE]

    # ── Paired design validation ──
    use_paired <- paired_design
    if (paired_design && n1 != n2) {
      warning("Comparison ", comp_label, ": paired_design = TRUE but n1 (", n1,
              ") \u2260 n2 (", n2, "). Falling back to unpaired test.")
      use_paired <- FALSE
    }
    if (use_paired) {
      message("  Paired design: samples must be in matched order — row 1 of '",
              g1, "' paired with row 1 of '", g2, "', etc.")
      # pair_ids: 1..n1 repeated for g1, then 1..n1 for g2 — used for connecting lines in boxplots
      pair_ids <- c(seq_len(n1), seq_len(n1))
    } else {
      pair_ids <- NULL
    }

    # ── Normality diagnostic (Shapiro-Wilk; summarised, not per-feature branching) ──
    shapiro_p <- sapply(seq_len(n_feat), function(j) {
      vals <- c(d1[, j], d2[, j])
      vals <- vals[is.finite(vals)]
      if (length(vals) < 3 || length(vals) > 5000) return(NA_real_)
      tryCatch(shapiro.test(vals)$p.value, error = function(e) NA_real_)
    })
    n_nonnormal  <- sum(!is.na(shapiro_p) & shapiro_p < 0.05)
    pct_nonnormal <- round(100 * n_nonnormal / n_feat, 1)
    message("  Normality (Shapiro-Wilk): ", n_nonnormal, "/", n_feat,
            " features (", pct_nonnormal, "%) non-normal at p < 0.05")
    if (pct_nonnormal > 50 && test_method == "t.test")
      message("  \u21b3 Tip: >50% non-normal. Consider setting test_method = 'wilcox.test'")

    # ── Statistical test ──
    p_vals <- stat_vals <- numeric(n_feat)
    wilcox_W <- rep(NA_real_, n_feat)  # store W for rank-biserial r
    for (j in seq_len(n_feat)) {
      res <- tryCatch(
        stat_fn(d1[, j], d2[, j],
                paired = use_paired, exact = FALSE, conf.int = FALSE),
        error = function(e) list(p.value = NA_real_, statistic = NA_real_)
      )
      p_vals[j]    <- res$p.value
      stat_vals[j] <- unname(res$statistic)
      if (test_method == "wilcox.test") wilcox_W[j] <- unname(res$statistic)
    }

    p_adj <- adjust_pvalues(p_vals)

    # ── Fold change ──
    agg_fn <- if (test_method == "wilcox.test") median else mean

    if (use_paired) {
      if (already_log_transformed || do_log_transform) {
        fc <- log_base^(apply(d1 - d2, 2, agg_fn, na.rm = TRUE))
      } else {
        fc <- apply(d1 / d2, 2, agg_fn, na.rm = TRUE)
      }
    } else {
      if (already_log_transformed || do_log_transform) {
        fc <- log_base^(apply(d1, 2, agg_fn, na.rm = TRUE) -
                         apply(d2, 2, agg_fn, na.rm = TRUE))
      } else {
        fc <- apply(d1, 2, agg_fn, na.rm = TRUE) /
              apply(d2, 2, agg_fn, na.rm = TRUE)
      }
    }

    # ── Effect size ─────────────────────────────────────────────────────────────
    mean1 <- colMeans(d1, na.rm = TRUE)
    mean2 <- colMeans(d2, na.rm = TRUE)
    sd1   <- apply(d1, 2, sd, na.rm = TRUE)
    sd2   <- apply(d2, 2, sd, na.rm = TRUE)
    df_pooled <- n1 + n2 - 2

    if (effect_size_method == "glass_delta") {
      # Glass's delta: (mean1 - mean2) / SD_reference
      # Reference = g2 (second group alphabetically = control by convention).
      # Preferred when Welch t-test is used and group SDs differ substantially
      # (heteroscedastic case where pooled SD would be misleading).
      ref_sd <- sd2
      ref_sd[ref_sd == 0] <- NA
      hedges_g <- (mean1 - mean2) / ref_sd
      # No Hedges's J correction for Glass's delta (not a pooled estimator)
      message("  Effect size: Glass's delta (reference = ", g2, ")")
    } else {
      # Hedges's g: df-weighted pooled SD with small-sample bias correction J.
      # s_p = sqrt(((n1-1)*s1² + (n2-1)*s2²) / (n1+n2-2))
      # J = 1 - 3/(4*df - 1)
      pooled_sd <- sqrt(((n1 - 1) * sd1^2 + (n2 - 1) * sd2^2) / df_pooled)
      pooled_sd[pooled_sd == 0] <- NA
      hedges_g  <- (mean1 - mean2) / pooled_sd
      J         <- 1 - 3 / (4 * df_pooled - 1)
      hedges_g  <- hedges_g * J
    }

    # Column name reflects actual method used
    es_col_name <- if (effect_size_method == "glass_delta") "Glass_delta" else "Hedges_g"

    # ── Rank-biserial correlation r when Wilcoxon selected ──
    # r_rb = (2W / (n1*n2)) - 1  →  [-1, 1]; |r| > 0.5 = large effect
    rank_biserial <- if (test_method == "wilcox.test") {
      ifelse(is.na(wilcox_W), NA_real_,
             (2 * wilcox_W / (n1 * n2)) - 1)
    } else {
      rep(NA_real_, n_feat)
    }

    # ── Power ──
    # Paired design: pwr.t.test(type="paired"); unpaired: pwr.t2n.test for unequal n.
    # Wilcoxon approximated via asymptotic relative efficiency (ARE ≈ π/3 ≈ 0.955).
    effect_for_power <- abs(hedges_g)
    if (test_method == "wilcox.test") effect_for_power <- effect_for_power * sqrt(pi / 3)
    stat_power <- suppressWarnings(
      sapply(effect_for_power, function(d) {
        if (is.na(d) || !is.finite(d)) return(NA_real_)
        tryCatch(
          if (use_paired)
            pwr.t.test(n = n1, d = d, sig.level = alpha, type = "paired")$power
          else
            pwr.t2n.test(n1, n2, d = d, sig.level = alpha)$power,
          error = function(e) NA_real_)
      })
    )

    # ── AUC (always ≥ 0.5: report max(AUC, 1-AUC) for direction-agnostic discrimination) ──
    auc_vals <- suppressMessages(
      sapply(seq_len(n_feat), function(j) {
        tryCatch({
          a <- as.numeric(roc(factor(sub_class), sub_data[, j],
                              quiet = TRUE)$auc)
          max(a, 1 - a)
        }, error = function(e) NA_real_)
      })
    )

    # ── Summary table ──
    stat_summary <- data.frame(
      ID     = if (!is.na(rt_col)) feat_info[[1]] else seq_len(n_feat),
      RT     = if (!is.na(rt_col)) as.numeric(feat_info[[rt_col]]) else NA,
      mz     = if (!is.na(mz_col)) as.numeric(feat_info[[mz_col]]) else NA,
      Name   = feat_names,
      Adduct = if (!is.na(add_col)) feat_info[[add_col]] else NA,
      n1     = n1,
      n2     = n2,
      Stat_value   = round(stat_vals, 3),
      p_value      = p_vals,
      p_adj        = p_adj,
      FoldChange   = round(fc, 4),
      log2FC       = round(log2(ifelse(is.na(fc) | fc <= 0, 1, fc)), 4),
      Effect_size   = round(hedges_g, 4),
      Rank_biserial = round(rank_biserial, 4),
      Power        = round(stat_power, 4),
      AUC          = round(auc_vals, 4),
      stringsAsFactors = FALSE
    )
    if (!is.null(precQC)) stat_summary$QC_CV_pct <- round(precQC, 1)

    # ── Lipid structural features (parsed from name) ──
    lf <- parse_lipid_features(feat_names)
    stat_summary <- cbind(stat_summary, lf)

    # Rename columns with context-appropriate labels
    colnames(stat_summary)[colnames(stat_summary) == "n1"]          <- paste0("n_", g1)
    colnames(stat_summary)[colnames(stat_summary) == "n2"]          <- paste0("n_", g2)
    colnames(stat_summary)[colnames(stat_summary) == "Effect_size"] <- es_col_name
    colnames(stat_summary)[10] <- paste0("p_adj_", p_adjust_method)
    colnames(stat_summary)[11] <- paste0("FC_", g1, "_vs_", g2)
    # Drop Rank_biserial column if t-test (all NA — no value in keeping it)
    if (test_method != "wilcox.test")
      stat_summary$Rank_biserial <- NULL
    # Add Ontology column for enrichment
    if (!is.na(ont_col)) stat_summary$Ontology <- feat_info[[ont_col]]

    save_table(stat_summary,
               file.path(comp_dir, paste0("statistical_results_", g1, "_vs_", g2, ".txt")))

    # ── Functional / enrichment analysis ──
    if (do_enrichment) {
      fc_col_name  <- paste0("FC_", g1, "_vs_", g2)
      padj_col_name <- paste0("p_adj_", p_adjust_method)
      enrich_dir   <- safe_dir(comp_dir, "enrichment")
      run_enrichment_analysis(stat_summary, "log2FC", padj_col_name,
                              enrich_dir, comp_label, class_colors)
      export_lion_input(stat_summary, "log2FC", padj_col_name, enrich_dir)
    }

    # ── p-value histogram ──
    p_hist <- ggplot(data.frame(p = p_vals), aes(x = p)) +
      geom_histogram(bins = 20, fill = "grey85", color = "white") +
      labs(title = comp_label, x = expression(italic(p)~value), y = "Count") +
      theme_embl()
    save_fig(p_hist, file.path(comp_dir, "p_value_histogram"), w_mm = 84, h_mm = 84)

    # ── Volcano plots ──
    fc_safe <- fc; fc_safe[is.na(fc_safe) | fc_safe == 0] <- 1
    ont_for_vol <- if (!is.na(ont_col)) feat_info[[ont_col]] else NULL
    save_fig(plot_volcano(log2(fc_safe), p_vals, title = paste(comp_label, "— raw p"),
                          class_vec = ont_for_vol),
             file.path(comp_dir, "volcano_raw_p"), w_mm = 120, h_mm = 100)
    save_fig(plot_volcano(log2(fc_safe), p_adj,
                          title = paste(comp_label, "— adj. p"), use_adj = TRUE,
                          class_vec = ont_for_vol),
             file.path(comp_dir, "volcano_adj_p"), w_mm = 120, h_mm = 100)

    # ── Subset PCA ──
    sub_colors <- class_colors[names(class_colors) %in% c(g1, g2)]
    save_fig(plot_pca(sub_data, sub_class, sub_colors, title = paste("PCA —", comp_label)),
             file.path(comp_dir, "PCA_subset"), w_mm = 140, h_mm = 120)
    save_fig(plot_pca(sub_data, sub_class, sub_colors,
                      title = paste("PCA scaled —", comp_label), scale_data = TRUE),
             file.path(comp_dir, "PCA_subset_scaled"), w_mm = 140, h_mm = 120)

    # ── Boxplots for significant features ──
    draw_p <- if (use_adj_p) p_adj else p_vals
    sig_idx <- which(draw_p < alpha)
    if (length(sig_idx) > 0) {
      box_dir <- safe_dir(comp_dir, "boxplots_significant")
      for (si in sig_idx) {
        fname_clean <- safe_filename_component(feat_names[si], max_chars = 80)
        stats_txt <- sprintf("p=%.4f | p.adj=%.4f | FC=%.2f | g=%.2f | power=%.2f",
                             p_vals[si], p_adj[si], fc[si], hedges_g[si], stat_power[si])
        bp <- plot_feature_boxplot(sub_data[, si], sub_class, feat_names[si],
                                   stats_txt, sub_colors,
                                   paired = use_paired, pairs = pair_ids)
        save_fig(bp, file.path(box_dir, paste0(si, "_", fname_clean)),
                 w_mm = 100, h_mm = 100)
      }
    }

    # ── Lipid-class FC boxplots (lipidomics only) ──
    if (data_type == "Lipidomics" && !is.na(ont_col)) {
      ont_vec <- feat_info[[ont_col]]

      save_fig(plot_class_fc(fc, ont_vec,
                             title = "log2 FC by Lipid Class — All",
                             subtitle = comp_label),
               file.path(comp_dir, "FC_by_class_all"), w_mm = 200, h_mm = 100)

      sig_raw <- which(p_vals < alpha)
      if (length(sig_raw) > 1) {
        save_fig(plot_class_fc(fc[sig_raw], ont_vec[sig_raw],
                               title = "log2 FC by Lipid Class — Significant (raw p)",
                               subtitle = paste0(comp_label, " | n=", length(sig_raw))),
                 file.path(comp_dir, "FC_by_class_sig_raw"), w_mm = 200, h_mm = 100)
      }
      sig_adj <- which(p_adj < alpha)
      if (length(sig_adj) > 1) {
        save_fig(plot_class_fc(fc[sig_adj], ont_vec[sig_adj],
                               title = "log2 FC by Lipid Class — Significant (adj. p)",
                               subtitle = paste0(comp_label, " | n=", length(sig_adj))),
                 file.path(comp_dir, "FC_by_class_sig_adj"), w_mm = 200, h_mm = 100)
      }
    }

    # ── Significance heatmaps ──
    heat_dir <- safe_dir(comp_dir, "heatmaps_significant")
    for (ptype in c("raw", "adj")) {
      sig <- which(if (ptype == "raw") p_vals < alpha else p_adj < alpha)
      if (length(sig) > 1) {
        label <- paste0(comp_label, " — ", if (ptype=="raw") "p" else "adj.p", " < ", alpha)
        make_heatmap(sub_data[, sig, drop = FALSE], sub_class,
                     feature_names = feat_names[sig], title = label,
                     filepath = file.path(heat_dir, paste0("heatmap_sig_", ptype)),
                     class_colors = sub_colors)
        make_heatmap(sub_data[, sig, drop = FALSE], sub_class,
                     feature_names = feat_names[sig],
                     title = paste(label, "(supervised)"),
                     filepath = file.path(heat_dir, paste0("heatmap_sig_", ptype, "_supervised")),
                     cluster_cols = FALSE, class_colors = sub_colors)
      }
    }
  }
}


# ══════════════════════════════════════════════════════════════════════════════
# 9. MASTER ANALYSIS PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

run_analysis <- function(data_mat, proc, method_name, outdir,
                         bmis_assignment = NULL) {
  message("\n══ Running analysis: ", method_name, " ══")
  sinfo     <- proc$sample_info
  finfo     <- proc$feature_info
  filetype  <- sinfo$Filetype
  class_vec <- sinfo$Class
  inj_order <- sinfo$InjOrder
  outdir    <- safe_dir(outdir)

  # ── Export normalized data in MS-DIAL format ──
  export_normalized_table(proc, data_mat, outdir, method_name)
  if (!is.null(bmis_assignment)) {
    save_table(bmis_assignment, file.path(outdir, "BMIS_assignment.csv"))
  }

  # ── QC Precision (pre-filter) ──
  qc_dir <- safe_dir(outdir, "QC_precision")
  precQC_pre <- apply(data_mat[filetype == "QC", , drop = FALSE], 2, cv_pct)

  save_fig(plot_qc_precision(precQC_pre, "QC Precision — before CV filter"),
           file.path(qc_dir, "QC_precision_original"), w_mm = 84, h_mm = 84)
  save_fig(plot_qc_histogram(precQC_pre, "QC CV — before CV filter"),
           file.path(qc_dir, "QC_CV_histogram_original"), w_mm = 84, h_mm = 84)

  # ── Injection-order drift plot ──
  if (any(!is.na(inj_order))) {
    drift_dir <- safe_dir(outdir, "QC_precision")
    save_fig(
      plot_injection_order_drift(data_mat, filetype, inj_order, class_vec,
                                  title_suffix = paste0(" — ", method_name)),
      file.path(drift_dir, "injection_order_drift"), w_mm = 200, h_mm = 100
    )
  }

  # ── CV filter ──
  data_filt <- data_mat
  finfo_filt <- finfo
  if (filter_by_cv && any(!is.na(precQC_pre))) {
    keep <- is.na(precQC_pre) | precQC_pre <= max_cv_percent
    n_removed <- sum(!keep)
    data_filt  <- data_mat[, keep, drop = FALSE]
    finfo_filt <- finfo[keep, , drop = FALSE]
    message("  CV filter: removed ", n_removed, " / ", ncol(data_mat),
            " features (CV > ", max_cv_percent, "%). Retained: ", ncol(data_filt))
  }

  # Post-filter QC precision
  precQC <- apply(data_filt[filetype == "QC", , drop = FALSE], 2, cv_pct)
  save_fig(plot_qc_precision(precQC, "QC Precision — after CV filter"),
           file.path(qc_dir, "QC_precision_filtered"), w_mm = 84, h_mm = 84)
  save_fig(plot_qc_histogram(precQC, "QC CV — after CV filter"),
           file.path(qc_dir, "QC_CV_histogram_filtered"), w_mm = 84, h_mm = 84)

  # ── Log transform ──
  data_log <- data_filt
  if (do_log_transform && !already_log_transformed) {
    data_log <- log(data_filt, base = log_base)
    data_log[!is.finite(as.matrix(data_log))] <- NA
  }

  # ── Strip IS from analytical data ──────────────────────────────────────────
  # IS features are spike-ins; their normalized intensities are not biologically
  # interpretable and must not be included in statistics, PCA, RLA, or heatmaps.
  # IS are kept only in the IS monitoring plot (handled separately).
  # Detection by name ("IS:") is robust against CV-filter reordering.
  if (proc$is_number > 0) {
    nc_is <- grep("Metabolite|name", colnames(finfo_filt), ignore.case = TRUE)[1]
    is_mask <- if (!is.na(nc_is))
      grepl("^IS:", finfo_filt[[nc_is]], ignore.case = FALSE)
    else
      rep(FALSE, nrow(finfo_filt))
    if (any(is_mask)) {
      data_log   <- data_log[, !is_mask, drop = FALSE]
      precQC     <- precQC[!is_mask]
      finfo_filt <- finfo_filt[!is_mask, , drop = FALSE]
      message("  IS features (n=", sum(is_mask),
              ") stripped from statistical data and exports")
    }
  }

  # ── Sort for RLA ──
  ord_class <- order(class_vec)
  data_class_sorted <- data_log[ord_class, , drop = FALSE]
  class_sorted      <- class_vec[ord_class]

  # Class colors (consistent across all plots)
  class_samples <- sort(unique(class_vec[filetype == "Sample"]))
  class_colors  <- assign_class_colors(class_vec)

  # ── RLA plots ──
  pca_dir <- safe_dir(outdir, "PCA_RLA")
  save_fig(plot_rla(data_class_sorted, class_sorted, "ag", class_colors),
           file.path(pca_dir, "RLA_across_group"), w_mm = 220, h_mm = 100)
  save_fig(plot_rla(data_class_sorted, class_sorted, "wg", class_colors),
           file.path(pca_dir, "RLA_within_group"), w_mm = 220, h_mm = 100)

  # ── PCA plots ──
  save_fig(plot_pca(data_log, class_vec, class_colors, "PCA — all samples"),
           file.path(pca_dir, "PCA_all"), w_mm = 160, h_mm = 130)
  save_fig(plot_pca(data_log, class_vec, class_colors,
                    "PCA scaled — all samples", scale_data = TRUE),
           file.path(pca_dir, "PCA_all_scaled"), w_mm = 160, h_mm = 130)

  samples_mask <- filetype == "Sample"
  if (sum(samples_mask) > 2) {
    sample_cls <- class_vec[samples_mask]
    sample_colors <- assign_class_colors(sample_cls)
    save_fig(plot_pca(data_log[samples_mask, ], sample_cls, sample_colors,
                      "PCA — samples only"),
             file.path(pca_dir, "PCA_samples"), w_mm = 160, h_mm = 130)
    save_fig(plot_pca(data_log[samples_mask, ], sample_cls, sample_colors,
                      "PCA scaled — samples only", scale_data = TRUE),
             file.path(pca_dir, "PCA_samples_scaled"), w_mm = 160, h_mm = 130)
  }

  # ── Sample outlier detection ──
  if (sum(samples_mask) > 4) {
    outlier_res <- tryCatch(
      detect_sample_outliers(data_log[samples_mask, ], class_vec[samples_mask],
                              filetype[samples_mask]),
      error = function(e) { message("  Outlier detection failed: ", e$message); NULL }
    )
    if (!is.null(outlier_res)) {
      save_fig(outlier_res$plot, file.path(pca_dir, "sample_outliers"),
               w_mm = 160, h_mm = 130)
      save_table(outlier_res$table, file.path(outdir, "sample_outliers.csv"))
      n_out <- sum(outlier_res$table$Outlier)
      if (n_out > 0)
        message("  \u26a0  Sample outliers flagged (n=", n_out, "): ",
                paste(outlier_res$table$Sample[outlier_res$table$Outlier], collapse = ", "))
      else
        message("  No sample outliers detected")
    }
  }

  # ── PCA loadings ──
  name_col_filt <- grep("Metabolite|name", colnames(finfo_filt), ignore.case = TRUE)[1]
  feat_names_filt <- if (!is.na(name_col_filt)) finfo_filt[[name_col_filt]] else colnames(data_log)
  save_pca_loadings(data_log, feat_names_filt, pca_dir,
                    title_suffix = paste0(" — ", method_name))

  # ── Lipid class heatmaps ──
  ont_col <- grep("Ontology|class", colnames(finfo_filt), ignore.case = TRUE)[1]
  if (data_type == "Lipidomics" && !is.na(ont_col)) {
    heat_class_dir <- safe_dir(outdir, "Heatmaps_lipid_class")
    ont_vec <- finfo_filt[[ont_col]]
    data_samples_log <- data_log[samples_mask, , drop = FALSE]
    sample_class <- class_vec[samples_mask]

    for (agg_fn_name in c("median", "mean")) {
      agg_fn <- if (agg_fn_name == "median") median else mean
      agg <- aggregate(t(data_samples_log), by = list(Ontology = ont_vec), FUN = agg_fn)
      rn <- agg$Ontology
      agg <- as.data.frame(t(agg[, -1]))
      colnames(agg) <- rn
      rownames(agg) <- rownames(data_samples_log)

      make_heatmap(as.matrix(agg), sample_class,
                   title = paste0("Lipid Classes — ", agg_fn_name),
                   filepath = file.path(heat_class_dir,
                                        paste0("heatmap_class_", agg_fn_name)),
                   class_colors = assign_class_colors(sample_class))
    }
  }

  # ── ANOVA (>2 groups) ──
  data_for_stats <- data_log[filetype != "QC", , drop = FALSE]
  class_for_stats <- class_vec[filetype != "QC"]

  if (length(unique(class_for_stats)) > 2) {
    anova_res <- run_anova(data_for_stats, class_for_stats, finfo_filt,
                           outdir, class_colors)
  }

  # ── Pairwise comparisons ──
  if (length(unique(class_for_stats)) >= 2) {
    run_pairwise(data_log, class_vec, filetype, finfo_filt, outdir,
                 class_colors, precQC)
  }

  message("  ✓ Analysis complete: ", method_name)
  invisible(list(precQC = precQC, data_log = data_log, finfo = finfo_filt))
}


# ── Export helper: write normalized data with feature metadata ──────────────
# Exports a clean samples × features table with feature metadata columns
# prepended.  The original MS-DIAL row order cannot be reconstructed after
# feature filtering (blank/unknown removal reindexes), so we export the
# curated feature set directly — this is more reliable than a partially-
# populated copy of the original table.

export_normalized_table <- function(proc, data_norm, outdir, method_name) {
  finfo <- proc$feature_info
  is_n  <- proc$is_number

  meta  <- finfo[, seq_len(min(ncol(finfo), proc$meta_col_end)), drop = FALSE]
  int_t <- as.data.frame(t(data_norm))
  colnames(int_t) <- rownames(data_norm)
  out_full <- cbind(meta, int_t)

  # Main export: endogenous features only (IS excluded).
  # Normalized IS intensities are not biologically interpretable.
  if (is_n > 0 && nrow(out_full) > is_n) {
    out_endo <- out_full[-seq_len(is_n), , drop = FALSE]
    save_table(out_endo, file.path(outdir,
               paste0("Normalized_", method_name, "_curated.txt")))
    # Full table (including IS) kept for reference / audit
    save_table(out_full, file.path(outdir,
               paste0("Normalized_", method_name, "_with_IS.txt")))
    message("  Export: IS excluded from _curated file (see _with_IS for full table)")
  } else {
    save_table(out_full, file.path(outdir,
               paste0("Normalized_", method_name, "_curated.txt")))
  }
}


# ══════════════════════════════════════════════════════════════════════════════
# 10. NORMALIZATION COMPARISON
# ══════════════════════════════════════════════════════════════════════════════

compare_normalizations <- function(norm_list, filetype, class_vec) {
  # norm_list: named list of data.frames (samples x features), pre-log
  # All must have the same dimensions and feature order
  methods <- names(norm_list)
  n_methods <- length(methods)
  if (n_methods < 2) {
    message("Only one normalization method — skipping comparison.")
    return(NULL)
  }

  qc_idx     <- which(filetype == "QC")
  sample_idx <- which(filetype != "QC")
  sample_class <- class_vec[sample_idx]
  groups     <- unique(sample_class)

  # ── Per-feature QC CV for each method ──
  cv_qc <- sapply(methods, function(m) {
    apply(norm_list[[m]][qc_idx, , drop = FALSE], 2, cv_pct)
  })

  # ── Per-feature intra-group CV (median across groups) ──
  cv_intra <- sapply(methods, function(m) {
    mat <- norm_list[[m]][sample_idx, , drop = FALSE]
    apply(mat, 2, function(x) {
      grp_cvs <- tapply(x, sample_class, cv_pct)
      median(grp_cvs, na.rm = TRUE)
    })
  })

  # ── Per-feature MAD in QCs ──
  mad_qc <- sapply(methods, function(m) {
    apply(norm_list[[m]][qc_idx, , drop = FALSE], 2, mad, na.rm = TRUE)
  })

  # ── D-ratio: within-group SD / total SD (lower = better separation) ──
  dratio <- sapply(methods, function(m) {
    mat <- norm_list[[m]][sample_idx, , drop = FALSE]
    apply(mat, 2, function(x) {
      within_sds <- tapply(x, sample_class, sd, na.rm = TRUE)
      total_sd   <- sd(x, na.rm = TRUE)
      if (is.na(total_sd) || total_sd == 0) return(NA_real_)
      median(within_sds, na.rm = TRUE) / total_sd
    })
  })

  # ── Summary table ──
  summary_tbl <- data.frame(
    Method          = methods,
    Median_QC_CV    = round(apply(cv_qc, 2, median, na.rm = TRUE), 2),
    Pct_CV_lt_20    = round(apply(cv_qc, 2, function(x) mean(x < 20, na.rm = TRUE) * 100), 1),
    Pct_CV_lt_30    = round(apply(cv_qc, 2, function(x) mean(x < 30, na.rm = TRUE) * 100), 1),
    Median_Intra_CV = round(apply(cv_intra, 2, median, na.rm = TRUE), 2),
    Median_MAD      = round(apply(mad_qc, 2, median, na.rm = TRUE), 2),
    Median_Dratio   = round(apply(dratio, 2, median, na.rm = TRUE), 3),
    stringsAsFactors = FALSE
  )

  # ── Statistical testing: Friedman + post-hoc Wilcoxon ──
  complete <- complete.cases(cv_qc)
  cv_comp  <- cv_qc[complete, , drop = FALSE]
  message("  Normalization comparison: ", nrow(cv_comp),
          " features with complete data across ", n_methods, " methods")

  friedman_res <- NULL
  pairwise_res <- NULL

  if (n_methods >= 3 && nrow(cv_comp) >= 10) {
    # Friedman test (non-parametric repeated measures across >= 3 methods)
    ft <- friedman.test(cv_comp)
    friedman_res <- list(statistic = ft$statistic, p.value = ft$p.value)
    message("  Friedman test: chi2 = ", round(ft$statistic, 2),
            ", p = ", format.pval(ft$p.value, digits = 3))

    if (ft$p.value < alpha) {
      # Post-hoc: pairwise Wilcoxon signed-rank with BH correction
      pairs <- combn(methods, 2)
      pw_p <- apply(pairs, 2, function(pr) {
        wilcox.test(cv_comp[, pr[1]], cv_comp[, pr[2]],
                    paired = TRUE, exact = FALSE)$p.value
      })
      pw_adj <- p.adjust(pw_p, method = "BH")
      pw_diff <- apply(pairs, 2, function(pr) {
        median(cv_comp[, pr[1]] - cv_comp[, pr[2]], na.rm = TRUE)
      })

      pairwise_res <- data.frame(
        Method1     = pairs[1, ],
        Method2     = pairs[2, ],
        Median_dCV  = round(pw_diff, 2),
        p_value     = pw_p,
        p_adj_BH    = pw_adj,
        Significant = ifelse(pw_adj < alpha, "yes", "no"),
        stringsAsFactors = FALSE
      )
    }
  } else if (n_methods == 2 && nrow(cv_comp) >= 10) {
    # Two methods: direct Wilcoxon signed-rank
    wt <- wilcox.test(cv_comp[, 1], cv_comp[, 2], paired = TRUE, exact = FALSE)
    md <- median(cv_comp[, 1] - cv_comp[, 2], na.rm = TRUE)
    pairwise_res <- data.frame(
      Method1     = methods[1],
      Method2     = methods[2],
      Median_dCV  = round(md, 2),
      p_value     = wt$p.value,
      p_adj_BH    = wt$p.value,
      Significant = ifelse(wt$p.value < alpha, "yes", "no"),
      stringsAsFactors = FALSE
    )
  }

  # ── Also test intra-group CV differences ──
  cv_intra_comp <- cv_intra[complete.cases(cv_intra), , drop = FALSE]
  intra_friedman <- NULL
  intra_pairwise <- NULL

  if (n_methods >= 3 && nrow(cv_intra_comp) >= 10) {
    ft2 <- friedman.test(cv_intra_comp)
    intra_friedman <- list(statistic = ft2$statistic, p.value = ft2$p.value)

    if (ft2$p.value < alpha) {
      pairs2 <- combn(methods, 2)
      pw_p2 <- apply(pairs2, 2, function(pr) {
        wilcox.test(cv_intra_comp[, pr[1]], cv_intra_comp[, pr[2]],
                    paired = TRUE, exact = FALSE)$p.value
      })
      pw_adj2 <- p.adjust(pw_p2, method = "BH")
      pw_diff2 <- apply(pairs2, 2, function(pr) {
        median(cv_intra_comp[, pr[1]] - cv_intra_comp[, pr[2]], na.rm = TRUE)
      })
      intra_pairwise <- data.frame(
        Method1     = pairs2[1, ],
        Method2     = pairs2[2, ],
        Median_dCV  = round(pw_diff2, 2),
        p_value     = pw_p2,
        p_adj_BH    = pw_adj2,
        Significant = ifelse(pw_adj2 < alpha, "yes", "no"),
        stringsAsFactors = FALSE
      )
    }
  } else if (n_methods == 2 && nrow(cv_intra_comp) >= 10) {
    wt2 <- wilcox.test(cv_intra_comp[, 1], cv_intra_comp[, 2],
                       paired = TRUE, exact = FALSE)
    md2 <- median(cv_intra_comp[, 1] - cv_intra_comp[, 2], na.rm = TRUE)
    intra_pairwise <- data.frame(
      Method1 = methods[1], Method2 = methods[2],
      Median_dCV = round(md2, 2), p_value = wt2$p.value,
      p_adj_BH = wt2$p.value,
      Significant = ifelse(wt2$p.value < alpha, "yes", "no"),
      stringsAsFactors = FALSE
    )
  }

  # ── Composite ranking (weights: QC CV 50%, D-ratio 30%, intra-group CV 20%) ──
  # Lower rank = better for all three metrics (smaller values are better).
  # NA D-ratio or intra-CV (e.g. single-group datasets) are set to median rank
  # so the composite is still informative.
  rank_qc    <- rank(summary_tbl$Median_QC_CV,    ties.method = "average", na.last = "keep")
  rank_intra <- rank(summary_tbl$Median_Intra_CV, ties.method = "average", na.last = "keep")
  rank_dr    <- rank(summary_tbl$Median_Dratio,   ties.method = "average", na.last = "keep")

  fill_na_rank <- function(r) { r[is.na(r)] <- median(r, na.rm = TRUE); r }
  rank_intra <- fill_na_rank(rank_intra)
  rank_dr    <- fill_na_rank(rank_dr)

  composite_rank <- 0.5 * rank_qc + 0.3 * rank_dr + 0.2 * rank_intra
  summary_tbl$Composite_Rank <- round(composite_rank, 2)

  best_idx <- which.min(composite_rank)
  best     <- summary_tbl$Method[best_idx]

  if (!is.null(pairwise_res)) {
    best_rows <- pairwise_res$Method1 == best | pairwise_res$Method2 == best
    sig_all   <- all(pairwise_res$Significant[best_rows] == "yes")
    sig_some  <- any(pairwise_res$Significant[best_rows] == "yes")
  } else {
    sig_all <- sig_some <- FALSE
  }

  recommendation <- paste0(
    "Recommended: ", best,
    " (composite rank = ", summary_tbl$Composite_Rank[best_idx],
    " | QC CV = ", summary_tbl$Median_QC_CV[best_idx],
    "% | D-ratio = ", summary_tbl$Median_Dratio[best_idx],
    " | intra-group CV = ", summary_tbl$Median_Intra_CV[best_idx], "%)",
    if (sig_all) " — significantly lower QC CV than all other methods"
    else if (sig_some) " — significantly lower QC CV than some methods; check pairwise table"
    else " — best composite rank but pairwise differences not statistically significant"
  )

  list(
    summary          = summary_tbl,
    cv_qc            = cv_qc,
    cv_intra         = cv_intra,
    mad_qc           = mad_qc,
    dratio           = dratio,
    friedman_qc      = friedman_res,
    pairwise_qc      = pairwise_res,
    friedman_intra   = intra_friedman,
    pairwise_intra   = intra_pairwise,
    recommendation   = recommendation
  )
}


# ── IS Monitoring Plot ────────────────────────────────────────────────────────
# Plots each IS intensity vs injection order, normalized to the per-IS QC median.
# QC samples are shown as filled circles; biological samples as open triangles.
# QC CV (%) is reported in each facet strip label.

plot_is_monitoring <- function(is_data, is_names, filetype, inj_order,
                               outdir = NULL, title_suffix = "") {
  if (is.null(is_data) || ncol(is_data) == 0) return(invisible(NULL))

  n_is   <- ncol(is_data)
  qc_idx <- which(filetype == "QC")

  # Compute QC median per IS for normalization; protect against zero/NA
  qc_medians <- apply(is_data[qc_idx, , drop = FALSE], 2, median, na.rm = TRUE)
  qc_medians[is.na(qc_medians) | qc_medians == 0] <- 1

  # Per-IS QC CV for facet annotation
  qc_cvs <- apply(is_data[qc_idx, , drop = FALSE], 2, function(x) {
    x <- x[is.finite(x) & x > 0]
    if (length(x) < 2) return(NA_real_)
    sd(x) / mean(x) * 100
  })

  # Build long data.frame (all samples)
  rows <- lapply(seq_len(n_is), function(k) {
    norm_int <- is_data[, k] / qc_medians[k]
    is_label <- sprintf("%s\n(QC CV = %.1f%%)",
                        is_names[k], ifelse(is.na(qc_cvs[k]), NA, qc_cvs[k]))
    data.frame(
      IS         = is_label,
      InjOrder   = inj_order,
      Intensity  = norm_int,
      SampleType = ifelse(filetype == "QC", "QC", "Sample"),
      stringsAsFactors = FALSE
    )
  })
  df <- do.call(rbind, rows)
  df$IS <- factor(df$IS, levels = unique(df$IS))

  # Determine facet layout: aim for ≤4 columns
  n_col <- min(4L, n_is)

  p <- ggplot(df, aes(x = InjOrder, y = Intensity, colour = SampleType,
                      shape = SampleType)) +
    geom_hline(yintercept = 1, linetype = "dashed", colour = "grey60", linewidth = 0.3) +
    geom_point(size = 1.8, alpha = 0.8, stroke = 0.4) +
    facet_wrap(~IS, ncol = n_col, scales = "free_y") +
    scale_colour_manual(values = c("QC" = "#E69F00", "Sample" = "#0072B2"),
                        name = "Sample type") +
    scale_shape_manual(values  = c("QC" = 19, "Sample" = 1),
                       name = "Sample type") +
    labs(title   = paste0("Internal Standard Monitoring", title_suffix),
         subtitle = "Intensity normalised to per-IS QC median",
         x = "Injection order",
         y = "Normalised intensity") +
    theme_embl() +
    theme(strip.text    = element_text(size = rel(0.75)),
          legend.position = "top")

  if (!is.null(outdir)) {
    h_mm <- 80 + 55 * ceiling(n_is / n_col)   # scale height with rows
    save_fig(p, file.path(outdir, "IS_monitoring"),
             w_mm = min(280, 65 * n_col + 20), h_mm = h_mm)
  }
  invisible(p)
}


# ── RLA Comparison Across Normalization Methods ───────────────────────────────
# Side-by-side across-group RLA boxplots for every method in norm_list.
# All panels share the same y-axis so inter-method differences are visible.
# Outliers are hidden; per-sample rate is annotated per panel.

plot_rla_comparison <- function(norm_list, filetype, class_vec,
                                outdir = NULL, title_suffix = "") {
  methods     <- names(norm_list)
  sample_mask <- filetype == "Sample"

  rows <- lapply(methods, function(m) {
    mat <- as.matrix(norm_list[[m]][sample_mask, , drop = FALSE])
    cv  <- class_vec[sample_mask]

    # log-transform (same base as pipeline setting; default log2)
    mat[mat <= 0] <- NA_real_
    mat <- log(mat, base = log_base)

    # Across-group RLA: subtract per-feature median
    feat_med <- apply(mat, 2, median, na.rm = TRUE)
    rla_mat  <- sweep(mat, 2, feat_med)

    samples <- rownames(mat)
    if (is.null(samples)) samples <- seq_len(nrow(mat))

    data.frame(
      Method  = m,
      Sample  = rep(samples, ncol(rla_mat)),
      Class   = rep(cv, ncol(rla_mat)),
      RLA     = as.vector(rla_mat),
      stringsAsFactors = FALSE
    )
  })
  df_all <- do.call(rbind, rows)
  df_all$Method <- factor(df_all$Method, levels = methods)
  df_all$Sample <- factor(df_all$Sample,
                           levels = unique(df_all$Sample[df_all$Method == methods[1]]))

  # Per-method outlier rate for subtitle annotations
  out_stats <- vapply(methods, function(m) {
    x <- df_all$RLA[df_all$Method == m]
    x <- x[is.finite(x)]
    q <- quantile(x, c(0.25, 0.75), na.rm = TRUE)
    iqr <- q[2] - q[1]
    round(mean(x < q[1] - 1.5 * iqr | x > q[2] + 1.5 * iqr, na.rm = TRUE) * 100, 1)
  }, numeric(1))

  # Build per-method facet label that shows outlier rate
  facet_labels <- setNames(
    sprintf("%s\n(outlier rate %.1f%%)", methods, out_stats),
    methods
  )

  # Use consistent color assignment (alphabetical, same as run_analysis)
  pal <- assign_class_colors(df_all$Class)

  p <- ggplot(df_all, aes(x = Sample, y = RLA, fill = Class)) +
    geom_hline(yintercept = 0, linetype = "dashed", colour = "grey50", linewidth = 0.4) +
    geom_boxplot(outlier.shape = NA, linewidth = 0.25, width = 0.7) +
    coord_cartesian(ylim = c(-2, 2)) +
    facet_wrap(~Method, ncol = 1, labeller = as_labeller(facet_labels)) +
    labs(title    = paste0("Across-group RLA — Method Comparison", title_suffix),
         subtitle = "Outliers (>1.5×IQR) hidden | y-axis fixed at ±2 for comparability",
         y = "Relative Log Abundance", x = NULL) +
    theme_embl() +
    theme(axis.text.x     = element_text(angle = 90, hjust = 1, vjust = 0.5, size = 4),
          strip.text       = element_text(size = rel(0.8), face = "bold"),
          legend.position  = "top",
          panel.spacing    = unit(4, "mm"))

  p <- p + scale_fill_manual(values = pal)

  if (!is.null(outdir)) {
    n_samples <- length(unique(df_all$Sample))
    h_mm <- 90 * length(methods) + 30   # ~90 mm per method panel
    w_mm <- min(400, max(200, n_samples * 3 + 40))
    save_fig(p, file.path(outdir, "RLA_comparison"),
             w_mm = w_mm, h_mm = h_mm)
  }
  invisible(p)
}


plot_norm_comparison <- function(comp, outdir) {
  outdir <- safe_dir(outdir)

  # ── Panel A: QC CV distribution violin + box ──
  cv_long <- as.data.frame(comp$cv_qc) %>%
    pivot_longer(everything(), names_to = "Method", values_to = "CV") %>%
    mutate(Method = factor(Method, levels = comp$summary$Method))

  p_cv <- ggplot(cv_long, aes(x = Method, y = CV, fill = Method)) +
    geom_violin(alpha = 0.5, scale = "width", color = NA) +
    geom_boxplot(width = 0.18, fill = "white", outlier.size = 0.4, linewidth = 0.3) +
    geom_hline(yintercept = 30, linetype = "dashed", color = "#D55E00", linewidth = 0.3) +
    geom_hline(yintercept = 20, linetype = "dashed", color = "#009E73", linewidth = 0.3) +
    coord_cartesian(ylim = c(0, min(100, quantile(cv_long$CV, 0.98, na.rm = TRUE) * 1.2))) +
    scale_fill_manual(values = CLASS_PALETTE[seq_along(comp$summary$Method)], guide = "none") +
    labs(title = "QC CV Distribution by Normalization Method",
         subtitle = "Dashed lines: 20% and 30% thresholds",
         y = "QC CV [%]", x = NULL) +
    theme_embl()

  # ── Panel B: % features below thresholds ──
  bar_data <- comp$summary %>%
    select(Method, Pct_CV_lt_20, Pct_CV_lt_30) %>%
    pivot_longer(-Method, names_to = "Threshold", values_to = "Pct") %>%
    mutate(Threshold = recode(Threshold,
                              Pct_CV_lt_20 = "CV < 20%",
                              Pct_CV_lt_30 = "CV < 30%"),
           Method = factor(Method, levels = comp$summary$Method))

  p_bar <- ggplot(bar_data, aes(x = Method, y = Pct, fill = Threshold)) +
    geom_col(position = position_dodge(0.7), width = 0.55, alpha = 0.85) +
    scale_fill_manual(values = c("CV < 20%" = "#009E73", "CV < 30%" = "#E69F00")) +
    scale_y_continuous(limits = c(0, 100), labels = label_percent(scale = 1)) +
    labs(title = "Features Below CV Thresholds",
         y = "% of features", x = NULL, fill = NULL) +
    theme_embl() +
    theme(legend.position = "top")

  # ── Panel C: D-ratio distribution ──
  dr_long <- as.data.frame(comp$dratio) %>%
    pivot_longer(everything(), names_to = "Method", values_to = "Dratio") %>%
    mutate(Method = factor(Method, levels = comp$summary$Method))

  p_dr <- ggplot(dr_long, aes(x = Method, y = Dratio, fill = Method)) +
    geom_violin(alpha = 0.5, scale = "width", color = NA) +
    geom_boxplot(width = 0.18, fill = "white", outlier.size = 0.4, linewidth = 0.3) +
    scale_fill_manual(values = CLASS_PALETTE[seq_along(comp$summary$Method)], guide = "none") +
    coord_cartesian(ylim = c(0, min(2, quantile(dr_long$Dratio, 0.98, na.rm = TRUE) * 1.2))) +
    labs(title = "D-ratio (within/total variation)",
         subtitle = "Lower = better biological separation",
         y = "D-ratio", x = NULL) +
    theme_embl()

  # ── Panel D: intra-group CV distribution ──
  intra_long <- as.data.frame(comp$cv_intra) %>%
    pivot_longer(everything(), names_to = "Method", values_to = "CV") %>%
    mutate(Method = factor(Method, levels = comp$summary$Method))

  p_intra <- ggplot(intra_long, aes(x = Method, y = CV, fill = Method)) +
    geom_violin(alpha = 0.5, scale = "width", color = NA) +
    geom_boxplot(width = 0.18, fill = "white", outlier.size = 0.4, linewidth = 0.3) +
    scale_fill_manual(values = CLASS_PALETTE[seq_along(comp$summary$Method)], guide = "none") +
    coord_cartesian(ylim = c(0, min(100, quantile(intra_long$CV, 0.98, na.rm = TRUE) * 1.2))) +
    labs(title = "Intra-group CV Distribution",
         subtitle = "Median per-feature CV within biological groups",
         y = "Intra-group CV [%]", x = NULL) +
    theme_embl()

  # ── Combine (2 × 2 layout) ──
  p_combined <- (p_cv | p_bar) / (p_dr | p_intra) +
    plot_annotation(tag_levels = "A",
                    title = "Normalization Method Comparison",
                    theme = theme(plot.title = element_text(face = "bold", size = 14)))

  save_fig(p_combined, file.path(outdir, "normalization_comparison"),
           w_mm = 280, h_mm = 240)

  # ── Individual panels for presentations ──
  save_fig(p_cv,    file.path(outdir, "QC_CV_violin"),         w_mm = 160, h_mm = 120)
  save_fig(p_bar,   file.path(outdir, "CV_threshold_barplot"), w_mm = 160, h_mm = 100)
  save_fig(p_dr,    file.path(outdir, "D_ratio_violin"),       w_mm = 160, h_mm = 120)
  save_fig(p_intra, file.path(outdir, "Intra_CV_violin"),      w_mm = 160, h_mm = 120)

  # ── Tables ──
  save_table(comp$summary, file.path(outdir, "normalization_summary.txt"))
  if (!is.null(comp$pairwise_qc))
    save_table(comp$pairwise_qc, file.path(outdir, "pairwise_QC_CV_tests.txt"))
  if (!is.null(comp$pairwise_intra))
    save_table(comp$pairwise_intra, file.path(outdir, "pairwise_intra_CV_tests.txt"))

  # ── Recommendation ──
  writeLines(c(
    "═══ Normalization Recommendation ═══",
    "",
    comp$recommendation,
    "",
    "─── Composite Score Summary (lower rank = better) ───",
    "    Weights: QC CV 50% | D-ratio 30% | Intra-group CV 20%",
    capture.output(print(comp$summary, row.names = FALSE)),
    "",
    if (!is.null(comp$friedman_qc))
      paste0("Friedman test (QC CV): chi2 = ", round(comp$friedman_qc$statistic, 2),
             ", p = ", format.pval(comp$friedman_qc$p.value, digits = 4)),
    if (!is.null(comp$friedman_intra))
      paste0("Friedman test (intra-group CV): chi2 = ", round(comp$friedman_intra$statistic, 2),
             ", p = ", format.pval(comp$friedman_intra$p.value, digits = 4)),
    "",
    "─── Pairwise QC CV Tests (Wilcoxon signed-rank, BH adjusted) ───",
    if (!is.null(comp$pairwise_qc)) capture.output(print(comp$pairwise_qc, row.names = FALSE))
    else "Not applicable (only 1 method or too few features)",
    "",
    "─── Pairwise Intra-group CV Tests ───",
    if (!is.null(comp$pairwise_intra)) capture.output(print(comp$pairwise_intra, row.names = FALSE))
    else "Not applicable",
    "",
    "─── Decision Framework (Drotleff et al., Anal. Chem.) ───",
    "  • If median QC CV already < 20%: normalization may not improve data quality",
    "  • TIC/mTIC: corrects global intensity drift; best if systematic bias is uniform",
    "  • BMIS: corrects class-specific variation using matched IS; requires IS data",
    "  • QC-RSC: corrects injection-order drift via LOESS on QC samples",
    "  • D-ratio < 0.4 indicates strong biological signal relative to technical noise",
    "  • Statistical significance alone is not sufficient — effect size matters",
    "  • Consider biological plausibility: normalization should not remove true signal",
    "",
    "─── Critical Appraisal Checklist ────────────────────────────────",
    "  ⚠ Verify annotations: MS-DIAL identifications are putative.",
    "    Confirm lipid identities with MS2 spectra and retention time evidence",
    "    before reporting results. Automated scores do not replace manual review.",
    "  ⚠ Lipid structural detail: MS-DIAL reports total composition (e.g. PC 36:2)",
    "    unless MS2 evidence is sufficient for sn-position resolution.",
    "    Do not over-interpret structural detail beyond what the data support.",
    "  ⚠ Multi-adduct features: the same lipid may appear as [M+H]+ and [M+Na]+.",
    "    Check whether adducts were summed or deduplicated upstream.",
    "  ⚠ Please stay critical: statistical outputs are a starting point,",
    "    not a conclusion. Biological context and effect size matter as much",
    "    as p-values."
  ), file.path(outdir, "recommendation.txt"))

  message("  ✓ Comparison results saved to: ", outdir)
  invisible(comp)
}


# ══════════════════════════════════════════════════════════════════════════════
# 11. MAIN EXECUTION
# ══════════════════════════════════════════════════════════════════════════════

if (!isTRUE(getOption("statpipeline.v2.source_only", FALSE))) {

pipeline_start_time <- proc.time()

message("\n╔══════════════════════════════════════════════════════╗")
message("║  MS-DIAL Statistical Pipeline v2.0 — Core Facility  ║")
message("╚══════════════════════════════════════════════════════╝\n")

# ── Load & preprocess ──
dat  <- load_msdial()
proc <- preprocess_data(dat)

int       <- proc$intensity
sinfo     <- proc$sample_info
filetype  <- sinfo$Filetype
class_vec <- sinfo$Class
inj_order <- sinfo$InjOrder

message("\nData: ", nrow(int), " samples x ", ncol(int), " features")
message("Groups: ", paste(unique(class_vec), collapse = ", "))
message("QC samples: ", sum(filetype == "QC"))

# ── Initialise pipeline log ──
run_log <- list(
  timestamp      = format(Sys.time(), "%Y-%m-%d %H:%M:%S"),
  input_file     = basename(dat$raw_table[1, 1]),   # first cell usually filename
  parameters     = list(
    data_type              = data_type,
    test_method            = test_method,
    effect_size_method     = effect_size_method,
    p_adjust_method        = p_adjust_method,
    alpha                  = alpha,
    fc_cutoff              = fc_cutoff,
    paired_design          = paired_design,
    log_transform          = do_log_transform,
    log_base               = log_base,
    impute_missing         = impute_missing,
    random_seed            = random_seed,
    filter_by_cv           = filter_by_cv,
    max_cv_percent         = max_cv_percent,
    remove_unknowns        = remove_unknowns,
    remove_blank_features  = remove_blank_features,
    min_blank_fold         = min_blank_fold,
    bmis_min_cv_improvement = bmis_min_cv_improvement
  ),
  samples = list(
    total  = nrow(int),
    qc     = sum(filetype == "QC"),
    groups = table(class_vec[filetype == "Sample"])
  ),
  features_raw  = ncol(dat$intensity),
  features_proc = ncol(int),
  is_count      = proc$is_number,
  normalisations_run = character(0)
)

# ── IS monitoring (raw intensities, before normalization) ──
if (!is.null(proc$is_data) && proc$is_number > 0) {
  is_mon_dir <- safe_dir("00_IS_Monitoring")
  plot_is_monitoring(proc$is_data, proc$is_names, filetype, inj_order,
                     outdir = is_mon_dir)
  message("→ IS monitoring plot saved to: 00_IS_Monitoring/")
}

# ── Build normalization list ──
norm_list <- list()
bmis_assign <- NULL

if (do_raw) {
  norm_list[["Raw"]] <- int
  message("\n→ Raw data prepared")
  run_log$normalisations_run <- c(run_log$normalisations_run, "Raw")
}

if (do_tic) {
  norm_list[["TIC"]] <- normalize_tic(int, proc$intensity_allfeatures)
  message("→ TIC normalization done")
}

if (do_mtic) {
  if (data_type == "Metabolomics")
    message("  Note: mTIC uses only annotated features. For metabolomics, consider",
            " do_tic=TRUE (full TIC) to avoid bias from unannotated background.")
  norm_list[["mTIC"]] <- normalize_mtic(int, proc$is_number)
  message("→ mTIC normalization done (IS excluded from denominator: n=",
          proc$is_number, ")")
}

if (do_bmis) {
  bmis_res <- normalize_bmis(int, filetype, proc$is_data, proc$is_names)
  if (!is.null(bmis_res$assignment)) {
    norm_list[["BMIS"]] <- bmis_res$data
    bmis_assign <- bmis_res$assignment
    message("→ BMIS normalization done (",
            sum(bmis_assign$Best_IS != "none"), "/", nrow(bmis_assign),
            " features assigned an IS)")
  }
}

if (do_qcrsc) {
  qcrsc_audit_dir <- safe_dir("00_QC-RSC_audit")
  norm_list[["QC-RSC"]] <- normalize_qcrsc(int, filetype, inj_order,
                                            outdir = qcrsc_audit_dir)
  run_log$normalisations_run <- c(run_log$normalisations_run, "QC-RSC")
  message("→ QC-RSC drift correction done")
}

# ── Normalization comparison (on same feature set, pre-log) ──
if (length(norm_list) >= 2) {
  message("\n── Comparing normalization methods ──")
  comp_result <- compare_normalizations(norm_list, filetype, class_vec)
  comp_dir    <- safe_dir("00_Normalization_Comparison")
  if (!is.null(comp_result)) {
    plot_norm_comparison(comp_result, comp_dir)
    message("\n", comp_result$recommendation, "\n")
  }

  # RLA comparison across all methods (side-by-side, same y-axis)
  plot_rla_comparison(norm_list, filetype, class_vec, outdir = comp_dir)
  message("→ RLA comparison plot saved to: 00_Normalization_Comparison/")
}

# ── Run full analysis for each method ──
method_counter <- 0
analysis_results <- list()

for (method_name in names(norm_list)) {
  method_counter <- method_counter + 1
  out_label <- sprintf("%02d_%s", method_counter, method_name)
  ba <- if (method_name == "BMIS") bmis_assign else NULL
  analysis_results[[method_name]] <- run_analysis(
    norm_list[[method_name]], proc, method_name, out_label, ba
  )
}

elapsed <- round((proc.time() - pipeline_start_time)["elapsed"], 1)
run_log$elapsed_seconds    <- elapsed
run_log$normalisations_run <- names(norm_list)
if (exists("comp_result") && !is.null(comp_result))
  run_log$recommended_normalisation <- comp_result$recommendation

# ── Write pipeline summary log ──
log_lines <- c(
  "══════════════════════════════════════════════════════════════════",
  "  MS-DIAL Statistical Pipeline v2.0 — Run Summary",
  "══════════════════════════════════════════════════════════════════",
  "",
  paste0("  Timestamp       : ", run_log$timestamp),
  paste0("  Elapsed time    : ", elapsed, " s"),
  "",
  "── Input ─────────────────────────────────────────────────────────",
  paste0("  File            : ", run_log$input_file),
  paste0("  Features (raw)  : ", run_log$features_raw),
  paste0("  Features (proc) : ", run_log$features_proc),
  paste0("  IS features     : ", run_log$is_count),
  paste0("  Total samples   : ", run_log$samples$total),
  paste0("  QC samples      : ", run_log$samples$qc),
  paste0("  Groups          : ",
         paste(paste0(names(run_log$samples$groups), " (n=",
                      run_log$samples$groups, ")"), collapse = ", ")),
  "",
  "── Parameters ────────────────────────────────────────────────────",
  paste0("  Data type       : ", run_log$parameters$data_type),
  paste0("  Statistical test: ", run_log$parameters$test_method),
  paste0("  Effect size     : ", run_log$parameters$effect_size_method),
  paste0("  p-adjustment    : ", run_log$parameters$p_adjust_method),
  paste0("  Alpha           : ", run_log$parameters$alpha),
  paste0("  FC cutoff (log2): ", run_log$parameters$fc_cutoff),
  paste0("  Paired design   : ", run_log$parameters$paired_design),
  paste0("  Log transform   : ", run_log$parameters$log_transform,
         if (run_log$parameters$log_transform)
           paste0(" (base ", run_log$parameters$log_base, ")") else ""),
  paste0("  Imputation      : ", run_log$parameters$impute_missing,
         if (run_log$parameters$impute_missing)
           paste0(" (seed=", run_log$parameters$random_seed, ")") else ""),
  paste0("  CV filter       : ", run_log$parameters$filter_by_cv,
         if (run_log$parameters$filter_by_cv)
           paste0(" (max ", run_log$parameters$max_cv_percent, "%)") else ""),
  paste0("  Remove unknowns : ", run_log$parameters$remove_unknowns),
  paste0("  Blank filter    : ", run_log$parameters$remove_blank_features,
         if (run_log$parameters$remove_blank_features)
           paste0(" (fold>=", run_log$parameters$min_blank_fold, ")") else ""),
  paste0("  BMIS min CV imp.: ", run_log$parameters$bmis_min_cv_improvement, "%"),
  "",
  "── Normalisations ────────────────────────────────────────────────",
  paste0("  Methods run     : ", paste(run_log$normalisations_run, collapse = ", ")),
  if (!is.null(run_log$recommended_normalisation))
    c("", "── Recommendation ───────────────────────────────────────────────",
      paste0("  ", run_log$recommended_normalisation))
  else character(0),
  "",
  "══════════════════════════════════════════════════════════════════"
)
writeLines(log_lines, "pipeline_summary.txt")

# ── Auto-generated Methods paragraph ──
log_base_name <- switch(as.character(round(log_base, 6)),
  "2.718282" = "natural logarithm (ln)",
  "2"        = "log2",
  "10"       = "log10",
  paste0("log base ", round(log_base, 4)))

norm_methods_txt <- paste(names(norm_list), collapse = ", ")

recommended_norm <- if (exists("comp_result") && !is.null(comp_result)) {
  sub(" \\(.*", "", sub("Recommended: ", "", comp_result$recommendation))
} else if (length(norm_list) == 1) {
  names(norm_list)[1]
} else {
  "not determined (only one method run)"
}

methods_para <- paste0(
  "Statistical analysis was performed using MS-DIAL StatPipeline v2.0 (Drotleff et al., ",
  format(Sys.Date(), "%Y"), "). ",
  "Raw MS-DIAL alignment data were preprocessed as follows: ",
  if (run_log$parameters$remove_unknowns)
    "features annotated as 'Unknown', 'RIKEN', or 'w/o MS2' were removed; " else "",
  if (run_log$parameters$remove_blank_features)
    paste0("features with a median QC-to-blank signal ratio < ",
           run_log$parameters$min_blank_fold, " were excluded as blank-derived; ") else "",
  if (run_log$parameters$impute_missing)
    paste0("missing values were imputed using random forest (missForest, seed = ",
           run_log$parameters$random_seed, "; Stekhoven & Bühlmann, 2012); ") else "",
  if (run_log$parameters$filter_by_cv)
    paste0("features with QC coefficient of variation (CV) > ",
           run_log$parameters$max_cv_percent, "% were removed. ") else "",
  if (run_log$parameters$log_transform)
    paste0("Intensities were ", log_base_name, "-transformed. ") else "",
  "The following normalization methods were evaluated: ",
  norm_methods_txt, ". ",
  "Methods were compared per-feature using QC CV, intra-group CV, ",
  "median absolute deviation (MAD), and D-ratio. ",
  "Statistical significance of differences between methods was assessed by Friedman test ",
  "with post-hoc pairwise Wilcoxon signed-rank tests (Benjamini-Hochberg correction). ",
  "The recommended normalization method was: ", recommended_norm, ". ",
  "Between-group statistical testing was performed using ",
  if (run_log$parameters$test_method == "wilcox.test")
    "the Wilcoxon rank-sum test (non-parametric). "
  else
    "Welch's two-sample t-test. ",
  "Multiple testing correction was applied using the ",
  toupper(run_log$parameters$p_adjust_method),
  " method (alpha = ", run_log$parameters$alpha, "). ",
  "Effect size was quantified as ",
  if (run_log$parameters$effect_size_method == "glass_delta")
    "Glass's delta (SD of reference group). "
  else
    "Hedges's g (pooled SD with small-sample bias correction). ",
  if (run_log$parameters$paired_design)
    "A paired analysis design was used. " else "",
  "Statistical power was estimated using the pwr package (Cohen, 1988). ",
  "Receiver operating characteristic area under the curve (AUC) was computed ",
  "per-feature using the pROC package. ",
  "Analyses were run in R (version ", R.version$major, ".", R.version$minor, ")."
)

writeLines(c(
  "══════════════════════════════════════════════════════════════════",
  "  Methods Paragraph (paste into manuscript)",
  "══════════════════════════════════════════════════════════════════",
  "",
  strwrap(methods_para, width = 90),
  "",
  "  ⚠ Fill in: journal-specific citation format, dataset/cohort details,",
  "    MS instrument, LC gradient, and any custom parameter deviations.",
  "══════════════════════════════════════════════════════════════════"
), "methods_writeup.txt")
message("  Methods paragraph saved to: methods_writeup.txt")
message("\n  Run summary saved to: pipeline_summary.txt")

message("\n╔══════════════════════════════════════════════════════╗")
message("║  Pipeline complete  (", elapsed, " s)                ")
message("╚══════════════════════════════════════════════════════╝")
message("Output directories:")
if (!is.null(proc$is_data) && proc$is_number > 0)
  message("  00_IS_Monitoring/")
for (i in seq_along(names(norm_list))) {
  message("  ", sprintf("%02d_%s/", i, names(norm_list)[i]))
}
if (length(norm_list) >= 2) message("  00_Normalization_Comparison/")
message("")

}
