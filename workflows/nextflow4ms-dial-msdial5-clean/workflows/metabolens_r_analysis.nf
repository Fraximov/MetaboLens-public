#!/usr/bin/env nextflow

/*
 * Secondary/manual R-only entrypoint.
 *
 * This workflow is useful for running modular StatPipeline v2 from an existing
 * table or result directory. It is not launched by the MetaboLens worker; the
 * app-facing worker entrypoint remains `main_msdial5.nf`.
 */

nextflow.enable.dsl=2

include {
    STATPIPELINE_V2_FROM_TABLE;
    STATPIPELINE_V2_STATS_ONLY;
    STATPIPELINE_V2_EXPORT_ONLY
} from '../subworkflows/statpipeline_v2/main'

def param_or_default(name, default_value) {
    return params.containsKey(name) ? params[name] : default_value
}

workflow {
    main:
    statpipeline_outputs = channel.empty()
    analysisMode = param_or_default('analysis_mode', 'full')
    supportedModes = ['full', 'qc_only', 'discovery', 'stats_only', 'export_only']
    if (!supportedModes.contains(analysisMode)) {
        error "Unsupported analysis_mode '${analysisMode}'. Expected one of: ${supportedModes.join(', ')}"
    }
    analysisConfig = [
        analysis_mode: analysisMode,
        output_prefix: param_or_default('output_prefix', 'metabolens'),
        keep_unknowns: analysisMode == 'discovery' ? true : (params.containsKey('keep_unknowns') ? params.keep_unknowns : false),
        remove_blank_features: params.containsKey('remove_blank_features') ? params.remove_blank_features : true,
        blank_filter_ratio: param_or_default('blank_filter_ratio', 5),
        filter_by_cv: params.containsKey('filter_by_cv') ? params.filter_by_cv : true,
        qc_cv_threshold: param_or_default('qc_cv_threshold', 30),
        imputation_method: param_or_default('imputation_method', 'missForest'),
        normalization_methods: param_or_default('normalization_methods', 'Raw,TIC,BMIS'),
        selected_normalization_method: param_or_default('selected_normalization_method', ''),
        run_stats: params.containsKey('run_stats') ? params.run_stats : true,
        contrasts: param_or_default('contrasts', ''),
        test_method: param_or_default('test_method', 't.test'),
        p_adjust_method: param_or_default('p_adjust_method', 'fdr'),
        run_lipid_module: params.containsKey('run_lipid_module') ? params.run_lipid_module : true,
        report_format: param_or_default('report_format', 'pdf')
    ]

    /*
     * This standalone workflow starts one directory below the workflow root
     * (`workflows/`), while the R wrappers live at the root.
     * Keep this explicit so local R-only tests use the same files as the
     * production main_msdial5.nf path.
     */
    workflow_root = "${workflow.projectDir}/.."
    r_dir = channel.fromPath("${workflow_root}/R", type: 'dir', checkIfExists: true)

    if (['full', 'qc_only', 'discovery'].contains(analysisMode)) {
        if (!params.msdial_table) {
            error "--msdial_table is required for analysis_mode=${analysisMode}"
        }

        msdial_table = channel.fromPath(params.msdial_table, checkIfExists: true)
        /*
         * The shared v2 subworkflow owns mode selection. In qc_only mode the
         * statistics/lipid processes are not scheduled; in full/discovery they
         * are included only when the corresponding run_* switches are enabled.
         */
        STATPIPELINE_V2_FROM_TABLE(msdial_table, r_dir, analysisConfig)
        statpipeline_outputs = STATPIPELINE_V2_FROM_TABLE.out.all_dirs
    } else if (analysisMode == 'stats_only') {
        if (!params.normalization_results) {
            error "--normalization_results is required for analysis_mode=stats_only"
        }
        // Stats-only starts from a previous normalize/ directory so users can
        // change comparisons without re-running MS-DIAL, parse, or preprocess.
        normalization_dir = channel.fromPath(params.normalization_results, type: 'dir', checkIfExists: true)
        STATPIPELINE_V2_STATS_ONLY(normalization_dir, r_dir, analysisConfig)
        statpipeline_outputs = STATPIPELINE_V2_STATS_ONLY.out.all_dirs
    } else if (analysisMode == 'export_only') {
        if (!params.existing_results) {
            error "--existing_results is required for analysis_mode=export_only"
        }
        // Export-only is intentionally shallow: it rebuilds provenance/report
        // outputs from existing result directories.
        existing_results = channel.fromPath(params.existing_results, type: 'dir', checkIfExists: true)
        STATPIPELINE_V2_EXPORT_ONLY(existing_results, r_dir, analysisConfig)
        statpipeline_outputs = STATPIPELINE_V2_EXPORT_ONLY.out.all_dirs
    }

    publish:
    statpipeline_outputs = statpipeline_outputs
}

output {
    statpipeline_outputs {
        path 'statpipeline'
    }
}
