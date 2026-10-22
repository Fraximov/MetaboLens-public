process build_rt_correction_plan {
    tag "internal-standard-rt-correction-plan"

    input:
    path is_qc_artifacts
    path planner_script
    path correction_module

    output:
    path "rt-correction/rt_correction_plan.json", emit: plan
    path "rt-correction/rt_correction_preview.json", emit: preview

    script:
    """
    mkdir -p rt-correction &&
    python3 ${planner_script} --is-qc-dir ${is_qc_artifacts} --outdir rt-correction
    """
}

process rewrite_mzml_retention_times {
    tag "approved-rt-correction"

    input:
    path prepared_data
    path correction_plan
    val selected_target_ids
    path rewriter_script
    path correction_module

    output:
    path "corrected_input", emit: corrected_data

    script:
    """
    mkdir -p corrected_input &&
    python3 ${rewriter_script} \
      --input-dir ${prepared_data} \
      --plan ${correction_plan} \
      --target-ids-json '${selected_target_ids}' \
      --outdir corrected_input
    """
}

workflow BUILD_RT_CORRECTION_REVIEW {
    take:
    is_qc_artifacts
    planner_script
    correction_module

    main:
    build_rt_correction_plan(is_qc_artifacts, planner_script, correction_module)

    emit:
    plan = build_rt_correction_plan.out.plan
    preview = build_rt_correction_plan.out.preview
    artifacts = build_rt_correction_plan.out.plan.mix(build_rt_correction_plan.out.preview)
}

workflow APPLY_RT_CORRECTION {
    take:
    prepared_data
    correction_plan
    selected_target_ids
    rewriter_script
    correction_module

    main:
    rewrite_mzml_retention_times(
        prepared_data,
        correction_plan,
        selected_target_ids,
        rewriter_script,
        correction_module
    )

    emit:
    corrected_data = rewrite_mzml_retention_times.out.corrected_data
}
