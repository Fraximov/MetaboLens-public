/*
 * MS-DIAL 5 engine module.
 *
 * This is the engine boundary. Everything MS-DIAL-specific lives here; the rest
 * of the pipeline -- RT correction, IS-QC, StatPipeline v2, canonical export,
 * SIRIUS -- consumes only the emits below and never MS-DIAL's native formats
 * directly. A second engine should supply the same emits from its own module
 * rather than branching inside main_msdial5.nf.
 *
 * Contract emitted by RUN_MSDIAL5_PREPARED_TO_STATS_TXT:
 *
 *   stats_txt            adapted table consumed by StatPipeline v2
 *   stats_report         adapter report JSON
 *   msdial_eic_archives  per-feature chromatogram archive read by the EIC viewer
 *   msdial_spectra       MSP export consumed by the SIRIUS branch
 *   msdial_artifacts     every native engine output, for registration
 *   stats_outputs        adapted table plus report, for registration
 *
 * Process names are part of the observable contract: the worker status reader
 * and the frontend metro map match on them, including workflow qualification.
 * `prepare_msdial5_input` appears unqualified because the entry workflow calls
 * it directly, while `peak_detection_msdial5` appears as
 * `RUN_MSDIAL5_PREPARED_TO_STATS_TXT:peak_detection_msdial5`. Moving a
 * definition between files does not change those names; changing which workflow
 * calls it does.
 *
 * RUN_MSDIAL5_TO_STATS_TXT is currently called by nothing. It is kept here
 * unchanged rather than deleted as part of a move-only refactor.
 */

include { prepareAcquisitionScript } from '../acquisition/main'

/*
 * Section: input preparation
 */
process prepare_msdial5_input {

    debug true

    input:
    path input_files
    path msdial_config

    output:
    path "prepared_input", emit: prepared_data
    path "prepared_msdial_config.txt", emit: prepared_msdial_config

    script:
    prepareAcquisitionScript(input_files) + """
    cp -L ${msdial_config} prepared_msdial_config.txt
    """
}

/*
 * Section: MS-DIAL execution
 */
process peak_detection_msdial5 {

    debug true

    input:
    path msdial_config
    path data
    path ms1_lib
    path ms2_lib

    output:
    path "ms-dial/AlignResult*.mdalign", emit: msdial_result
    path "ms-dial/AlignResult*.mdmsp", emit: msdial_spectra
    path "ms-dial/AlignResult*.mzTabM", emit: msdial_mztabm
    path "ms-dial/AlignResult*.EIC.aef", emit: msdial_eic, optional: true
    stdout emit: msdial_result_log

    script:
    """
    export TMPDIR="\$PWD/tmp" &&
    export TMP="\$TMPDIR" &&
    export TEMP="\$TMPDIR" &&
    mkdir -p "\$TMPDIR" &&
    echo "peak detection via MS-DIAL 5 software" &&
    mkdir "ms-dial" &&
    cp -L ${msdial_config} "ms-dial" &&
    cp -L ${ms1_lib} "ms-dial" &&
    cp -L ${ms2_lib} "ms-dial" &&
    cp -rL ${data} "ms-dial" &&
    cd "ms-dial" &&
    command -v MSDIALCUI >/dev/null &&
    MSDIALCUI lcms -i ${data} -o ./ -m ${msdial_config} &&
    for eic_file in *EIC.aef; do
      if [ -f "\${eic_file}" ]; then
        normalized_name="\${eic_file##*\\\\}"
        if [ "\${normalized_name}" != "\${eic_file}" ]; then
          cp -L "\${eic_file}" "\${normalized_name}"
        fi
      fi
    done
    """
}

/*
 * Section: MS-DIAL-to-stats adapter
 */
process adapt_mdalign_to_stats_txt {

    debug true

    input:
    path mdalign_file
    path samplesheet
    val factor_column
    path adapter_script

    output:
    path "stats/*_adapted.txt", emit: stats_txt
    path "stats/*_adapter_report.json", emit: stats_report

    script:
    """
    mkdir -p stats &&
    python3 ${adapter_script} \
      --mdalign ${mdalign_file} \
      --samplesheet ${samplesheet} \
      --out "stats/\$(basename ${mdalign_file} .mdalign)_adapted.txt" \
      --factor-column "${factor_column}" \
      --report-json "stats/\$(basename ${mdalign_file} .mdalign)_adapter_report.json"
    """
}

workflow RUN_MSDIAL5_PREPARED_TO_STATS_TXT {
    take:
    prepared_data
    prepared_msdial_config
    ms1_library
    ms2_library
    samplesheet
    factor_column
    adapter_script

    main:
    /*
     * Contract step 2: execute MS-DIAL 5.
     *
     * The output is MS-DIAL's native alignment artifacts. The modular R path
     * does not consume these directly; it consumes the adapted text table
     * created in the next step.
     */
    peak_detection_msdial5(
        prepared_msdial_config,
        prepared_data,
        ms1_library,
        ms2_library
    )

    /*
     * Contract step 3: bridge MS-DIAL into the existing facility stats format.
     *
     * The adapter attaches MetaboLens sample metadata from samplesheet.csv and
     * emits one *_adapted.txt table for modular StatPipeline v2.
     */
    adapt_mdalign_to_stats_txt(
        peak_detection_msdial5.out.msdial_result,
        samplesheet,
        factor_column,
        adapter_script
    )

    emit:
    stats_txt = adapt_mdalign_to_stats_txt.out.stats_txt
    stats_report = adapt_mdalign_to_stats_txt.out.stats_report
    msdial_eic_archives = peak_detection_msdial5.out.msdial_eic
    msdial_spectra = peak_detection_msdial5.out.msdial_spectra
    msdial_artifacts = peak_detection_msdial5.out.msdial_result
        .mix(peak_detection_msdial5.out.msdial_spectra)
        .mix(peak_detection_msdial5.out.msdial_mztabm)
        .mix(peak_detection_msdial5.out.msdial_eic)
    stats_outputs = adapt_mdalign_to_stats_txt.out.stats_txt
        .mix(adapt_mdalign_to_stats_txt.out.stats_report)
}

workflow RUN_MSDIAL5_TO_STATS_TXT {
    take:
    input_files
    msdial_config
    ms1_library
    ms2_library
    samplesheet
    factor_column
    adapter_script

    main:
    /*
     * Contract step 1: materialize MS-DIAL-readable inputs.
     *
     * MetaboLens/worker has already decided which files belong to this assay
     * run and has written a run-specific MS-DIAL config. This process only
     * converts/copies those files into the shape expected by MSDIALCUI.
     */
    prepare_msdial5_input(
        input_files.collect(),
        msdial_config
    )

    RUN_MSDIAL5_PREPARED_TO_STATS_TXT(
        prepare_msdial5_input.out.prepared_data,
        prepare_msdial5_input.out.prepared_msdial_config,
        ms1_library,
        ms2_library,
        samplesheet,
        factor_column,
        adapter_script
    )

    emit:
    stats_txt = RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.stats_txt
    stats_report = RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.stats_report
    msdial_eic_archives = RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.msdial_eic_archives
    msdial_artifacts = RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.msdial_artifacts
    stats_outputs = RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.stats_outputs
    msdial_spectra = RUN_MSDIAL5_PREPARED_TO_STATS_TXT.out.msdial_spectra
}
