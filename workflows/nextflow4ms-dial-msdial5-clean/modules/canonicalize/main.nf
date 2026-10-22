/*
 * Canonical export boundary.
 *
 * These processes wrap already-produced MS-DIAL, adapter, and StatPipeline
 * files into stable MetaboLens TSV/JSON outputs. The Python exporter does not
 * alter upstream MS-DIAL/R behavior and does not recompute scientific results.
 */

process CANONICALIZE_WITH_STATPIPELINE {

    tag "${run_id}"

    input:
    path stats_table
    path statpipeline_dirs
    path params_json
    val run_id
    path exporter_script

    output:
    path "${run_id}/canonical", emit: canonical_dir

    script:
    """
    python3 ${exporter_script} \
      --run-id "${run_id}" \
      --params-json ${params_json} \
      --stats-table ${stats_table} \
      --search-root . \
      --outdir "${run_id}/canonical"
    """
}

process CANONICALIZE_FROM_STATS_ONLY {

    tag "${run_id}"

    input:
    path stats_table
    path params_json
    val run_id
    path exporter_script

    output:
    path "${run_id}/canonical", emit: canonical_dir

    script:
    """
    python3 ${exporter_script} \
      --run-id "${run_id}" \
      --params-json ${params_json} \
      --stats-table ${stats_table} \
      --outdir "${run_id}/canonical"
    """
}
