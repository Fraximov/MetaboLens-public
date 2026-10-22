"""Validate native-format MassCube CSV using the engine's parameter schema."""
import csv
import io
import math

from app.core.engine_contract import validate_engine_parameters


MAX_TEMPLATE_BYTES = 64 * 1024


def validate_masscube_parameters(values: dict, schema: dict) -> dict:
    values = validate_engine_parameters(schema, {'masscube_parameters': values})['masscube_parameters']
    for key, value in values.items():
        if isinstance(value, (int, float)) and not math.isfinite(value):
            raise ValueError(f'MassCube {key} must be finite')
    # Missing bounds retain the pinned MassCube 1.2.22 defaults.
    for prefix, upper in (('rt', 10000), ('mz', 100000)):
        if values.get(f'{prefix}_lower_limit', 0) >= values.get(f'{prefix}_upper_limit', upper):
            raise ValueError(f'MassCube {prefix}_lower_limit must be less than {prefix}_upper_limit')
    return values


def parse_masscube_template(content: str, schema: dict) -> dict:
    if not content.strip() or len(content.encode('utf-8')) > MAX_TEMPLATE_BYTES:
        raise ValueError(f'MassCube template must be nonempty UTF-8 CSV of at most {MAX_TEMPLATE_BYTES} bytes')
    rows = csv.reader(io.StringIO(content), strict=True)
    properties = schema['properties']['masscube_parameters']['properties']
    values = {}
    try:
        header = next(rows)
        if [column.strip().lower() for column in header[:2]] != ['parameter', 'value'] or len(header) not in (2, 3):
            raise ValueError('MassCube CSV must start with parameter,value and an optional description column')
        for row in rows:
            if not row:
                continue
            if len(row) < 2 or len(row) > len(header) or not row[1].strip():
                raise ValueError(f'MassCube CSV row {rows.line_num} needs a parameter and value')
            key, text = row[0].strip(), row[1].strip()
            if key not in properties or key in values:
                raise ValueError(f'Unknown or duplicate MassCube parameter: {key}')
            kind = properties[key]['type']
            if kind in ('number', 'integer'):
                try:
                    value = float(text)
                except ValueError as exc:
                    raise ValueError(f'MassCube {key} must be numeric') from exc
                if kind == 'integer' and math.isfinite(value) and value.is_integer():
                    value = int(value)
            elif kind == 'boolean':
                if text.lower() not in ('true', 'false', 'yes', 'no'):
                    raise ValueError(f'MassCube {key} must be true or false')
                value = text.lower() in ('true', 'yes')
            else:
                value = text
            values[key] = value
    except csv.Error as exc:
        raise ValueError(f'Invalid MassCube CSV at row {rows.line_num}: {exc}') from exc
    if not values:
        raise ValueError('MassCube CSV must contain at least one parameter')
    return validate_masscube_parameters(values, schema)
