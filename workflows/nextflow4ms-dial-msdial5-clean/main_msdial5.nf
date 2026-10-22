#!/usr/bin/env nextflow

/*
 * Legacy worker entrypoint.
 *
 * This is the active worker-used Nextflow entrypoint for MetaboLens.
 * The backend seed currently references `main_msdial5.nf`.
 * Do not rename or move this file without migrating worker submission, docs,
 * tests, and existing run/resume expectations together.
 *
 * Dataflow:
 * input prep -> MS-DIAL -> adapter -> StatPipeline v2 -> canonical outputs
 *
 * Future annotation modules should consume canonical outputs rather than
 * modifying the MS-DIAL/R scientific core.
 */

nextflow.enable.dsl=2

/*
 * Section: StatPipeline v2
 */
include {
    STATPIPELINE_V2_FROM_TABLE;
    STATPIPELINE_V2_STATS_ONLY;
    STATPIPELINE_V2_EXPORT_ONLY
} from './subworkflows/statpipeline_v2/main'

/*
 * Section: canonical export
 */
include {
    CANONICALIZE_WITH_STATPIPELINE;
    CANONICALIZE_FROM_STATS_ONLY
} from './modules/canonicalize/main'

/*
 * Section: IS-QC review gate
 */
include {
    RUN_IS_QC
} from './modules/is_qc/main'

include {
    BUILD_RT_CORRECTION_REVIEW;
    APPLY_RT_CORRECTION
} from './modules/rt_correction/main'


/*
    * Section: SIRIUS annotation
    */
include {
    RUN_SIRIUS
} from './modules/sirius/main'

/*
 * MS-DIAL 5-only variant of RUMP for untargeted metabolomics data processing.
 *
 * This entrypoint is intentionally thin: process definitions describe
 * executable steps, named subworkflows describe graph composition, and the
 * entry workflow below owns launch validation.
 */

/*
 * Section: MS-DIAL engine
 */
include {
    prepare_msdial5_input;
    RUN_MSDIAL5_PREPARED_TO_STATS_TXT;
    RUN_MSDIAL5_TO_STATS_TXT
} from './modules/msdial/main'

/*
 * Section: main workflow
 */
workflow {
    main:
    msdial_artifacts = channel.empty()
    is_qc_artifacts = channel.empty()
    rt_correction_artifacts = channel.empty()
    stats_outputs = channel.empty()
    v2_statpipeline_outputs = channel.empty()
    canonical_outputs = channel.empty()
    sirius_outputs = channel.empty()

    /*
     * Section: params/config
     */
    pipelineVersion = '1.0dev-msdial5'
    pipelineTimestamp = '20260320'
    msdial5ContainerImage = params.msdial5_container_image ?: 'metabolens-public-msdial5:unconfigured'
    thermoRawParserImage = params.thermorawfileparser_image ?: 'quay.io/biocontainers/thermorawfileparser@sha256:2b07a010c46fdc2c0cc918344c7400aaf7b5aec93ae645d2f9cafae43b513ada'
    statsAdapterContainerImage = params.stats_adapter_container_image ?: 'metabolens-public-stats-adapter:unconfigured'
    statsPipelineV2ContainerImage = params.stats_pipeline_v2_container_image ?: 'metabolens-public-statpipeline-v2:unconfigured'
    isQcContainerImage = params.is_qc_container_image ?: 'metabolens-public-is-qc:unconfigured'
    workflowStage = params.workflow_stage ?: 'full'
    applyRtCorrection = params.containsKey('apply_rt_correction') && params.apply_rt_correction.toString().toBoolean()
    rtCorrectionTargetIds = params.containsKey('rt_correction_target_ids') ? params.rt_correction_target_ids.toString() : 'null'
    needsRtCorrectionReview = workflowStage == 'is_qc_review' || applyRtCorrection
    analysisMode = params.analysis_mode ?: 'full'
    supportedWorkflowStages = ['full', 'is_qc_review', 'msdial_review']
    supportedAnalysisModes = ['full', 'qc_only', 'discovery', 'stats_only', 'export_only']
    enableSirius = params.containsKey('enable_sirius') && params.enable_sirius.toString().toBoolean()
    siriusContainerImage = params.sirius_container_image ?: 'metabolens-public-sirius:unconfigured'
    siriusAnnotationMode = params.sirius_annotation_mode ?: 'formulas'
    siriusStructureDb = params.sirius_structure_db ?: 'all'
    siriusMs2Tolerance = params.sirius_ms2_tolerance ?: 5
    siriusMs1Tolerance = params.sirius_ms1_tolerance ?: 2
    siriusStructureExpansiveSearch = params.sirius_structure_expansive_search ?: 'OFF'
    siriusRecompute = params.sirius_recompute ?: false

    if (!supportedWorkflowStages.contains(workflowStage)) {
        error "Unsupported workflow_stage '${workflowStage}'. Expected one of: ${supportedWorkflowStages.join(', ')}"
    }
    if (!supportedAnalysisModes.contains(analysisMode)) {
        error "Unsupported analysis_mode '${analysisMode}'. Expected one of: ${supportedAnalysisModes.join(', ')}"
    }
    if (['is_qc_review', 'msdial_review'].contains(workflowStage) && ['stats_only', 'export_only'].contains(analysisMode)) {
        error "workflow_stage=${workflowStage} cannot be combined with analysis_mode=${analysisMode}"
    }
    isQcConfig = [
        mz_tolerance_da: params.is_qc_mz_tolerance_da ?: 0.01,
        rt_tolerance_min: params.is_qc_rt_tolerance_min ?: 0.5,
        min_peak_height: params.is_qc_min_peak_height ?: 1000,
        min_sample_pass_fraction: params.is_qc_min_sample_pass_fraction ?: 0.8,
        min_target_pass_fraction: params.is_qc_min_target_pass_fraction ?: 0.8,
        max_qc_cv_percent: params.is_qc_max_qc_cv_percent ?: 30
    ]
    analysisConfig = [
        analysis_mode: analysisMode,
        output_prefix: params.output_prefix ?: 'metabolens',
        keep_unknowns: analysisMode == 'discovery' ? true : (params.containsKey('keep_unknowns') ? params.keep_unknowns : false),
        remove_blank_features: params.containsKey('remove_blank_features') ? params.remove_blank_features : true,
        blank_filter_ratio: params.blank_filter_ratio ?: 5,
        filter_by_cv: params.containsKey('filter_by_cv') ? params.filter_by_cv : true,
        qc_cv_threshold: params.qc_cv_threshold ?: 30,
        imputation_method: params.imputation_method ?: 'missForest',
        normalization_methods: params.normalization_methods ?: 'Raw,TIC,BMIS',
        selected_normalization_method: params.selected_normalization_method ?: '',
        run_stats: params.containsKey('run_stats') ? params.run_stats : true,
        contrasts: params.contrasts ?: '',
        test_method: params.test_method ?: 't.test',
        p_adjust_method: params.p_adjust_method ?: 'fdr',
        run_lipid_module: params.containsKey('run_lipid_module') ? params.run_lipid_module : true,
        report_format: params.report_format ?: 'pdf'
    ]


    if (params.help) {
        System.out.println("")
        System.out.println("MS-DIAL 5-only variant for untargeted metabolomics data analysis - Version: ${pipelineVersion} (${pipelineTimestamp})")
        System.out.println("This variant runs MS-DIAL 5 only and does not execute MS-FLO post-processing.")
        System.out.println("")
        System.out.println("Usage:")
        System.out.println("   nextflow run main_msdial5.nf -profile [options: functional_test; docker; singularity]")
        System.out.println("")
        System.out.println("Arguments:")
        System.out.println("    -profile                                docker, singularity, or functional_test")
        System.out.println("    --msdial5_container_image               container image that provides MSDIALCUI")
        System.out.println("    --thermorawfileparser_image             container used to convert Thermo RAW files to mzML")
        System.out.println("    --stats_adapter_container_image         container image that adapts MS-DIAL outputs to the stats input contract")
        System.out.println("    --is_qc_container_image                 container image that extracts internal-standard QC traces")
        System.out.println("    --samplesheet                           workflow-generated sample metadata CSV used by downstream adapter steps")
        System.out.println("    --is_library                            tab-separated internal-standard target list")
        System.out.println("    --stats_factor_column                   samplesheet column used as the biological Class label in the adapted stats input")
        System.out.println("    --workflow_stage                        full, is_qc_review, or msdial_review; review stages stop at their review boundary")
        System.out.println("    --stats_pipeline_v2_container_image     container image that runs the modular StatPipeline v2 wrappers")
        System.out.println("    --analysis_mode                         full, qc_only, discovery, stats_only, or export_only")
        System.out.println("    --help                                  whether to show help information or not, default is null")
        System.out.println("")
        exit 1
    }

    customRunName = params.name
    if (!(workflow.runName ==~ /[a-z]+_[a-z]+/)) {
        customRunName = workflow.runName
    }
    runId = params.run_id ?: (workflow.runName ?: customRunName)
    paramsJson = channel.fromPath(params.params_json ?: "${workflow.projectDir}/assets/empty_params.json", checkIfExists: true)
    canonicalExporter = channel.fromPath("${workflow.projectDir}/adapters/canonicalize_outputs.py", checkIfExists: true)

    summary = [:]
    summary['Pipeline Name']  = 'RUMP MS-DIAL 5'
    if (workflow.revision) summary['Pipeline Release'] = workflow.revision
    summary['Run Name']         = customRunName ?: workflow.runName
    summary['Input']            = params.input_dir ?: params.input ?: 'n/a'
    summary['MS-DIAL 5 image']  = msdial5ContainerImage
    summary['RAW converter']    = thermoRawParserImage
    summary['Stats adapter']    = statsAdapterContainerImage
    summary['Stats pipeline']   = statsPipelineV2ContainerImage
    summary['IS-QC image']      = isQcContainerImage
    summary['Workflow stage']   = workflowStage
    summary['Analysis mode']    = analysisMode
    summary['Max Resources']    = "$params.max_memory memory, $params.max_cpus cpus, $params.max_time time per job"
    if (workflow.containerEngine) summary['Container engine'] = workflow.containerEngine
    summary['Output dir']       = params.outdir
    summary['Launch dir']       = workflow.launchDir
    summary['Working dir']      = workflow.workDir
    summary['Script dir']       = workflow.projectDir
    summary['User']             = workflow.userName
    summary['Config Profile']   = workflow.profile
    summary['Config Files']     = workflow.configFiles.join(', ')
    log.info summary.collect { k,v -> "${k.padRight(18)}: $v" }.join("\n")
    log.info "-\033[2m--------------------------------------------------\033[0m-"

    if (analysisMode == 'stats_only') {
        if (!params.normalization_results) {
            error "--normalization_results is required for analysis_mode=stats_only"
        }

        r_dir = channel.fromPath("${workflow.projectDir}/R", type: 'dir', checkIfExists: true)
        normalization_dir = channel.fromPath(params.normalization_results, type: 'dir', checkIfExists: true)
        STATPIPELINE_V2_STATS_ONLY(normalization_dir, r_dir, analysisConfig)
        v2_statpipeline_outputs = STATPIPELINE_V2_STATS_ONLY.out.all_dirs
    } else if (analysisMode == 'export_only') {
        if (!params.existing_results) {
            error "--existing_results is required for analysis_mode=export_only"
        }

        r_dir = channel.fromPath("${workflow.projectDir}/R", type: 'dir', checkIfExists: true)
        existing_results = channel.fromPath(params.existing_results, type: 'dir', checkIfExists: true)
        STATPIPELINE_V2_EXPORT_ONLY(existing_results, r_dir, analysisConfig)
        v2_statpipeline_outputs = STATPIPELINE_V2_EXPORT_ONLY.out.all_dirs
    } else {
        /*
         * Contract step 1: shared input preparation.
         *
         * Review-gate resumes reuse this exact process cache so a full resume
         * can continue after IS-QC without repeating RAW conversion when
         * Nextflow can reuse the prepared mzML directory.
         */
        prepare_msdial5_input(
            channel.fromPath("${params.input_dir}/*", checkIfExists: true).collect(),
            channel.fromPath(params.msdial_config, checkIfExists: true)
        )

        preparedDataForMsdial = prepare_msdial5_input.out.prepared_data

        if (needsRtCorrectionReview) {
            log.info "Building cached internal-standard QC and RT-correction review artifacts."
            RUN_IS_QC(
                prepare_msdial5_input.out.prepared_data,
                channel.fromPath(params.is_library, checkIfExists: true),
                channel.fromPath(params.samplesheet, checkIfExists: true),
                channel.fromPath("${workflow.projectDir}/adapters/extract_is_qc.py", checkIfExists: true),
                channel.value(isQcConfig)
            )
            BUILD_RT_CORRECTION_REVIEW(
                RUN_IS_QC.out.artifacts,
                channel.fromPath("${workflow.projectDir}/adapters/build_rt_correction_plan.py", checkIfExists: true),
                channel.fromPath("${workflow.projectDir}/adapters/rt_correction.py", checkIfExists: true)
            )
            is_qc_artifacts = RUN_IS_QC.out.artifacts
            rt_correction_artifacts = BUILD_RT_CORRECTION_REVIEW.out.artifacts

            if (workflowStage != 'is_qc_review' && applyRtCorrection) {
                APPLY_RT_CORRECTION(
                    prepare_msdial5_input.out.prepared_data,
                    BUILD_RT_CORRECTION_REVIEW.out.plan,
                    rtCorrectionTargetIds,
                    channel.fromPath("${workflow.projectDir}/adapters/rewrite_mzml_rt.py", checkIfExists: true),
                    channel.fromPath("${workflow.projectDir}/adapters/rt_correction.py", checkIfExists: true)
                )
                preparedDataForMsdial = APPLY_RT_CORRECTION.out.corrected_data
            }
        }

        if (workflowStage != 'is_qc_review') {
            RUN_MSDIAL5_PREPARED_TO_STATS_TXT(
                preparedDataForMsdial,
                prepare_msdial5_input.out.prepared_msdial_config,
                channel.fromPath(params.ms1_library, checkIfExists: true),
                channel.fromPath(params.ms2_library, checkIfExists: true),
                channel.fromPath(params.samplesheet, checkIfExists: true),
                channel.value(params.stats_factor_column ?: 'group_label'),
                channel.fromPath("${workflow.projectDir}/adapters/mdalign_to_stats_txt.py", checkIfExists: true)
            )
            msdial_artifacts = RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.msdial_artifacts
            stats_outputs = RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.stats_outputs
        }

        if (workflowStage == 'msdial_review') {
            log.info "workflow_stage=msdial_review selected; skipping downstream StatPipeline after MS-DIAL outputs and the adapted stats handoff table."
            CANONICALIZE_FROM_STATS_ONLY(
                RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.stats_txt,
                paramsJson,
                channel.value(runId),
                canonicalExporter
            )
            canonical_outputs = CANONICALIZE_FROM_STATS_ONLY.out.canonical_dir
        } else if (workflowStage == 'full') {
            /*
             * Contract step 4: modular StatPipeline v2.
             *
             * The subworkflow owns the analysis_mode graph. qc_only therefore
             * schedules parse/preprocess/normalize/QC/report only, while full and
             * discovery add statistics/lipids only when their switches are enabled.
             */

            if (enableSirius) {
                log.info( "enable_sirius=true selected; preparing MS-DIAL spectra for SIRIUS and running SIRIUS CLI.")
                RUN_SIRIUS(
                    RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.msdial_spectra,
                    channel.fromPath("${workflow.projectDir}/adapters/prepare_sirius_msp.py", checkIfExists: true),
                    channel.value(params.sirius_workspace ?: ''),
                    channel.value(siriusAnnotationMode),
                    channel.value(siriusStructureDb),
                    channel.value(siriusMs2Tolerance),
                    channel.value(siriusMs1Tolerance),
                    channel.value(siriusStructureExpansiveSearch),
                    channel.value(siriusRecompute)
                )
                sirius_outputs = RUN_SIRIUS.out.annotation_input_msp
                    .mix(RUN_SIRIUS.out.annotation_input_summary)
                    .mix(RUN_SIRIUS.out.artifacts)
            }

            r_dir = channel.fromPath("${workflow.projectDir}/R", type: 'dir', checkIfExists: true)
            STATPIPELINE_V2_FROM_TABLE(
                RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.stats_txt,
                r_dir,
                analysisConfig
            )
            v2_statpipeline_outputs = STATPIPELINE_V2_FROM_TABLE.out.all_dirs

            /*
             * Section: future annotation seam.
             *
             * Contract step 5: canonical MetaboLens output boundary.
             *
             * This adapter consumes already-produced MS-DIAL/StatPipeline files
             * and writes stable TSV/JSON outputs for future annotation and
             * database-ingest layers. It does not alter upstream scientific work.
             *
             * Future extension seam: providers such as SIRIUS, GNPS/GNPS2, and MZmine
             * should consume canonical outputs instead of changing this MS-DIAL/R core.
            */
            CANONICALIZE_WITH_STATPIPELINE(
                RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.stats_txt,
                STATPIPELINE_V2_FROM_TABLE.out.all_dirs.collect(),
                paramsJson,
                channel.value(runId),
                canonicalExporter
            )
            canonical_outputs = CANONICALIZE_WITH_STATPIPELINE.out.canonical_dir
        }
    }

    publish:
    msdial_artifacts = msdial_artifacts
    is_qc_artifacts = is_qc_artifacts
    rt_correction_artifacts = rt_correction_artifacts
    stats_outputs = stats_outputs
    v2_statpipeline_outputs = v2_statpipeline_outputs
    canonical_outputs = canonical_outputs
    sirius_outputs = sirius_outputs
}

output {
    msdial_artifacts {
        path '.'
    }
    is_qc_artifacts {
        path '.'
    }
    rt_correction_artifacts {
        path '.'
    }
    stats_outputs {
        path '.'
    }
    v2_statpipeline_outputs {
        path 'statpipeline'
    }
    canonical_outputs {
        path '.'
    }
    sirius_outputs {
        path 'sirius'
    }
}
