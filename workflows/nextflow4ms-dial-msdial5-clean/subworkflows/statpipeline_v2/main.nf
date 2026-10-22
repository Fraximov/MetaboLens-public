include { R_PARSE_MSDIAL } from '../../modules/r_parse_msdial/main'
include { R_PREPROCESS } from '../../modules/r_preprocess/main'
include { R_NORMALIZE } from '../../modules/r_normalize/main'
include { R_QC_REPORT } from '../../modules/r_qc_report/main'
include { R_STATISTICS } from '../../modules/r_statistics/main'
include { R_LIPID_SUMMARY } from '../../modules/r_lipid_summary/main'
include { R_FINAL_REPORT } from '../../modules/r_final_report/main'

/*
 * Shared StatPipeline v2 graph.
 *
 * Processes remain one scientific stage each; this subworkflow owns which
 * stages belong to each mode. That keeps the Nextflow DAG honest: qc_only does
 * not schedule biological statistics or lipid summaries, while full/discovery
 * can enable those stages through explicit parameters.
 */

def statpipeline_bool(value, default_value = false) {
    if (value == null) {
        return default_value
    }
    if (value instanceof Boolean) {
        return value
    }
    return value.toString().trim().toLowerCase() in ['true', '1', 'yes', 'y']
}

def statpipeline_mode(analysis_config) {
    return analysis_config.analysis_mode ?: 'full'
}

def statpipeline_runs_statistics(analysis_config) {
    return statpipeline_mode(analysis_config) != 'qc_only' && statpipeline_bool(analysis_config.run_stats, true)
}

def statpipeline_runs_lipids(analysis_config) {
    return statpipeline_mode(analysis_config) != 'qc_only' && statpipeline_bool(analysis_config.run_lipid_module, true)
}

workflow STATPIPELINE_V2_FROM_TABLE {
    take:
    msdial_table
    r_dir
    analysis_config

    main:
    R_PARSE_MSDIAL(msdial_table, r_dir, analysis_config)
    R_PREPROCESS(R_PARSE_MSDIAL.out.parse_dir, r_dir, analysis_config)
    R_NORMALIZE(R_PREPROCESS.out.preprocess_dir, r_dir, analysis_config)
    R_QC_REPORT(R_NORMALIZE.out.normalization_dir, r_dir, analysis_config)

    final_inputs = R_PARSE_MSDIAL.out.parse_dir
        .mix(R_PREPROCESS.out.preprocess_dir)
        .mix(R_NORMALIZE.out.normalization_dir)
        .mix(R_QC_REPORT.out.qc_dir)

    if (statpipeline_runs_statistics(analysis_config)) {
        R_STATISTICS(R_NORMALIZE.out.normalization_dir, r_dir, analysis_config)
        final_inputs = final_inputs.mix(R_STATISTICS.out.statistics_dir)
    }

    if (statpipeline_runs_lipids(analysis_config)) {
        R_LIPID_SUMMARY(R_PREPROCESS.out.preprocess_dir, r_dir, analysis_config)
        final_inputs = final_inputs.mix(R_LIPID_SUMMARY.out.lipid_dir)
    }

    R_FINAL_REPORT(final_inputs.collect(), r_dir, analysis_config)
    all_dirs = final_inputs.mix(R_FINAL_REPORT.out.final_report_dir)

    emit:
    final_report_dir = R_FINAL_REPORT.out.final_report_dir
    all_dirs = all_dirs
}

workflow STATPIPELINE_V2_STATS_ONLY {
    take:
    normalization_dir
    r_dir
    analysis_config

    main:
    R_STATISTICS(normalization_dir, r_dir, analysis_config)
    R_FINAL_REPORT(R_STATISTICS.out.statistics_dir.collect(), r_dir, analysis_config)
    all_dirs = R_STATISTICS.out.statistics_dir.mix(R_FINAL_REPORT.out.final_report_dir)

    emit:
    final_report_dir = R_FINAL_REPORT.out.final_report_dir
    all_dirs = all_dirs
}

workflow STATPIPELINE_V2_EXPORT_ONLY {
    take:
    existing_results
    r_dir
    analysis_config

    main:
    R_FINAL_REPORT(existing_results.collect(), r_dir, analysis_config)

    emit:
    final_report_dir = R_FINAL_REPORT.out.final_report_dir
    all_dirs = R_FINAL_REPORT.out.final_report_dir
}
