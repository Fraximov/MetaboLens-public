#!/usr/bin/env nextflow
// Integration entrypoint. Existing main_msdial5.nf remains available for old jobs.
nextflow.enable.dsl = 2

params.metabolensr_config = "${projectDir}/assets/metabolensr_config.json"
params.metabolensr_overrides = "${projectDir}/assets/automatic_overrides.tsv"
params.metabolensr_container_image = null

include { prepare_msdial5_input; peak_detection_msdial5 } from './modules/msdial/main'
include { RUN_IS_QC } from './modules/is_qc/main'
include { BUILD_RT_CORRECTION_REVIEW; APPLY_RT_CORRECTION } from './modules/rt_correction/main'
include { RUN_SIRIUS } from './modules/sirius/main'
include { MSDIAL_TO_METABOLENSR } from './modules/metabolensr/msdial_bridge'
include { METABOLENSR_QC } from './modules/metabolensr/main'

workflow {
    main:
    workflowStage = params.workflow_stage ?: 'full'
    if (!(workflowStage in ['full', 'is_qc_review', 'msdial_review', 'qc_preview'])) {
        error "Unsupported workflow_stage: ${workflowStage}"
    }
    if (workflowStage != 'is_qc_review' && !params.metabolensr_container_image) {
        error 'Required: --metabolensr_container_image with the tested immutable image reference'
    }
    if (!(params.analysis_mode in ['full', 'qc_only'])) {
        error 'MetaboLensR provides QC/preprocessing; legacy statistical analysis modes are unsupported'
    }
    if (!(params.stats_factor_column ==~ /[A-Za-z_][A-Za-z0-9_]*/)) {
        error 'stats_factor_column must name a samplesheet column'
    }
    applyRtCorrection = params.apply_rt_correction.toString().toBoolean()
    rtCorrectionTargetIds = params.containsKey('rt_correction_target_ids') ? params.rt_correction_target_ids.toString() : 'null'
    needsRtCorrectionReview = workflowStage == 'is_qc_review' || applyRtCorrection
    is_qc_artifacts = channel.empty()
    rt_correction_artifacts = channel.empty()
    msdial_artifacts = channel.empty()
    sirius_outputs = channel.empty()
    isQcConfig = [
        mz_tolerance_da: params.is_qc_mz_tolerance_da,
        rt_tolerance_min: params.is_qc_rt_tolerance_min,
        min_peak_height: params.is_qc_min_peak_height,
        min_sample_pass_fraction: params.is_qc_min_sample_pass_fraction,
        min_target_pass_fraction: params.is_qc_min_target_pass_fraction,
        max_qc_cv_percent: params.is_qc_max_qc_cv_percent
    ]
    enableSirius = params.enable_sirius.toString().toBoolean()
    siriusAnnotationMode = params.sirius_annotation_mode
    siriusStructureDb = params.sirius_structure_db
    siriusMs2Tolerance = params.sirius_ms2_tolerance
    siriusMs1Tolerance = params.sirius_ms1_tolerance
    siriusStructureExpansiveSearch = params.sirius_structure_expansive_search
    siriusRecompute = params.sirius_recompute
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
        peak_detection_msdial5(
            prepare_msdial5_input.out.prepared_msdial_config,
            preparedDataForMsdial,
            channel.fromPath(params.ms1_library, checkIfExists: true),
            channel.fromPath(params.ms2_library, checkIfExists: true)
        )
        msdial_artifacts = peak_detection_msdial5.out.msdial_result
            .mix(peak_detection_msdial5.out.msdial_spectra)
            .mix(peak_detection_msdial5.out.msdial_mztabm)
            .mix(peak_detection_msdial5.out.msdial_eic)
        alignments = peak_detection_msdial5.out.msdial_result.flatten().map { alignment ->
            def id = alignment.baseName
            if (!(id ==~ /[A-Za-z0-9_.-]+/)) error "Unsupported alignment filename: ${id}"
            tuple(id, alignment)
        }
        MSDIAL_TO_METABOLENSR(
            alignments,
            channel.fromPath(params.samplesheet, checkIfExists: true),
            params.stats_factor_column,
            channel.fromPath("${projectDir}/adapters/mdalign_to_metabolensr.py", checkIfExists: true)
        )
        METABOLENSR_QC(
            MSDIAL_TO_METABOLENSR.out.table,
            channel.fromPath(params.metabolensr_config, checkIfExists: true),
            channel.fromPath(params.metabolensr_overrides, checkIfExists: true),
            workflowStage in ['msdial_review', 'qc_preview']
        )
        if (enableSirius && workflowStage == 'full') {
            log.info( "enable_sirius=true selected; preparing MS-DIAL spectra for SIRIUS and running SIRIUS CLI.")
            RUN_SIRIUS(
                peak_detection_msdial5.out.msdial_spectra,
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

    }
    publish:
    msdial_artifacts = msdial_artifacts
    is_qc_artifacts = is_qc_artifacts
    rt_correction_artifacts = rt_correction_artifacts
    sirius_outputs = sirius_outputs
}

output {
    // Avoid replacing identical files held open by shared-volume readers on resume.
    msdial_artifacts { path '.'; overwrite 'deep' }
    is_qc_artifacts { path '.'; overwrite 'deep' }
    rt_correction_artifacts { path '.'; overwrite 'deep' }
    sirius_outputs { path 'sirius'; overwrite 'deep' }
}
