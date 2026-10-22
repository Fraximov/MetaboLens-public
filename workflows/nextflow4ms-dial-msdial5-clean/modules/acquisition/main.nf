// Shared RAW conversion and native-input staging; no processing-engine config.
def prepareAcquisitionScript(input_files) {
    def files = input_files instanceof Collection ? input_files : [input_files]
    def quotedFiles = files.collect { "'" + it.toString().replace("'", "'\"'\"'") + "'" }.join(' ')
    """
    export TMPDIR="\$PWD/tmp"
    export TMP="\$TMPDIR"
    export TEMP="\$TMPDIR"
    mkdir -p "\$TMPDIR" prepared_input

    for input_file in ${quotedFiles}; do
        file_name=\$(basename "\$input_file")
        lower_name=\$(printf '%s' "\$file_name" | tr '[:upper:]' '[:lower:]')
        if [[ "\$lower_name" == *.raw ]]; then
            ThermoRawFileParser -i="\$input_file" -o=prepared_input -f=1 -l=1
        elif [[ "\$lower_name" == *.mzml || "\$lower_name" == *.abf ]]; then
            cp -L "\$input_file" prepared_input/
        else
            echo "Skipping unsupported input file: \$file_name"
        fi
    done
    """
}

process prepare_acquisition_input {
    container params.thermorawfileparser_image
    cpus 1
    memory '4 GB'
    time '1800m'
    cache 'lenient'

    input:
    path input_files

    output:
    path 'prepared_input', emit: prepared_data

    script:
    prepareAcquisitionScript(input_files)
}
