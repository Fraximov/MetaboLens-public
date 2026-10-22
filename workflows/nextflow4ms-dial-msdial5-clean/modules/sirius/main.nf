/*
 * SIRIUS annotation module.
 *
 * V1 prepares MS-DIAL alignment spectra for SIRIUS and runs the SIRIUS CLI.
 * It does not merge SIRIUS annotations back into canonical tables yet.
 */

process prepare_sirius_msp {
    tag "${mdmsp_file.simpleName}"

    input:
    path mdmsp_file
    path adapter_script

    output:
    path "annotation-input/sirius_input.msp", emit: msp_file
    path "annotation-input/sirius_input_summary.json", emit: summary

    script:
    """
    mkdir -p annotation-input
    python3 ${adapter_script} \
        --input ${mdmsp_file} \
        --output annotation-input/sirius_input.msp \
        --summary-json annotation-input/sirius_input_summary.json
    """
}

process run_sirius {
    tag "${sirius_msp_file.simpleName}"

    input:
    path sirius_msp_file
    val sirius_workspace
    val sirius_annotation_mode
    val sirius_structure_db
    val sirius_ms2_tolerance
    val sirius_ms1_tolerance
    val sirius_structure_expansive_search
    val sirius_recompute

    output:
    path "sirius_output", emit: artifacts

    script:
    def shellQuote = { value -> "'" + value.toString().replace("'", "'\"'\"'") + "'" }
    """
    #!/usr/bin/env bash
    set -euo pipefail

    mkdir -p sirius_output/summaries sirius_output/logs
    : > sirius_output/logs/sirius.stdout.log
    : > sirius_output/logs/sirius.stderr.log

    command -v sirius >/dev/null 2>&1 || { echo >&2 "SIRIUS is not installed. Please install SIRIUS to run this workflow."; exit 1; }

    mode=${shellQuote(sirius_annotation_mode)}
    structure_db=${shellQuote(sirius_structure_db)}
    expansive_search=${shellQuote(sirius_structure_expansive_search)}
    workspace=${shellQuote(sirius_workspace)}

    case "\${mode}" in
        formulas|canopus|structures|full) ;;
        *)
            echo "Invalid SIRIUS annotation mode: \${mode}. Expected one of: formulas, canopus, structures, full." >&2
            exit 1
            ;;
    esac

    case "\${expansive_search}" in
        OFF|EXACT|APPROXIMATE) ;;
        *)
            echo "Invalid SIRIUS structure expansive search option: \${expansive_search}. Expected one of: OFF, EXACT, APPROXIMATE." >&2
            exit 1
            ;;
    esac

    workspace_args=()
    if [ -n "\${workspace}" ]; then
        workspace_args=(--workspace "\${workspace}")
    fi

    recompute_args=()
    if [ ${shellQuote(sirius_recompute)} = "true" ]; then
        recompute_args=(--recompute)
    fi

    run_sirius_step() {
        local step_name="\$1"
        shift

        local stdout_log="sirius_output/logs/\${step_name}.stdout.log"
        local stderr_log="sirius_output/logs/\${step_name}.stderr.log"

        echo "Running SIRIUS step: \${step_name}"

        set +e
        sirius "\${workspace_args[@]}" "\${recompute_args[@]}" "\$@" > "\${stdout_log}" 2> "\${stderr_log}"
        local status="\$?"
        set -e

        cat "\${stdout_log}" >> sirius_output/logs/sirius.stdout.log
        cat "\${stderr_log}" >> sirius_output/logs/sirius.stderr.log
        cat "\${stdout_log}"
        cat "\${stderr_log}" >&2

        if grep -R "Login ERROR" sirius_output/logs >/dev/null; then
            echo "SIRIUS login is required before annotations can be written." >&2
            exit 1
        fi

        if [ "\${status}" -ne 0 ]; then
            echo "SIRIUS step '\${step_name}' failed with exit code \${status}." >&2
            exit "\${status}"
        fi
    }

    run_sirius_step formulas \
        --input sirius_input.msp \
        --project sirius_output/project.sirius \
        formulas --ppm-max ${shellQuote(sirius_ms1_tolerance)} --ppm-max-ms2 ${shellQuote(sirius_ms2_tolerance)}

    if [ "\${mode}" = "canopus" ] || [ "\${mode}" = "structures" ] || [ "\${mode}" = "full" ]; then
        run_sirius_step fingerprints \
            --project sirius_output/project.sirius \
            fingerprints
    fi

    if [ "\${mode}" = "canopus" ] || [ "\${mode}" = "full" ]; then
        run_sirius_step classes \
            --project sirius_output/project.sirius \
            classes
    fi

    if [ "\${mode}" = "structures" ] || [ "\${mode}" = "full" ]; then
        structure_args=(structures -d "\${structure_db}")
        if [ "\${expansive_search}" != "OFF" ]; then
            structure_args+=(-e "\${expansive_search}")
        fi

        run_sirius_step structures \
            --project sirius_output/project.sirius \
            "\${structure_args[@]}"
    fi

    run_sirius_step summaries \
        --project sirius_output/project.sirius \
        summaries \
        --output sirius_output/summaries

    if ! find sirius_output/summaries -type f | grep -q .; then
        echo "SIRIUS completed without writing summary files to sirius_output/summaries." >&2
        exit 1
    fi
    """
}


workflow RUN_SIRIUS {
    take:
    mdmsp_file
    adapter_script
    sirius_workspace
    sirius_annotation_mode
    sirius_structure_db
    sirius_ms2_tolerance
    sirius_ms1_tolerance
    sirius_structure_expansive_search
    sirius_recompute

    main:
    prepare_sirius_msp(mdmsp_file, adapter_script)
    run_sirius(prepare_sirius_msp.out.msp_file, sirius_workspace, sirius_annotation_mode, sirius_structure_db, sirius_ms2_tolerance, sirius_ms1_tolerance, sirius_structure_expansive_search, sirius_recompute)

    emit:
    annotation_input_msp = prepare_sirius_msp.out.msp_file
    annotation_input_summary = prepare_sirius_msp.out.summary
    artifacts = run_sirius.out.artifacts
}
