"""split metabolomics assay subtype tables

Revision ID: 6f4c3a2b1d0e
Revises: 4c2d8e1f9a6b
Create Date: 2026-04-16 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6f4c3a2b1d0e"
down_revision: Union[str, None] = "4c2d8e1f9a6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assays", sa.Column("platform", sa.String(length=100), nullable=True, server_default="metabolomics"))
    op.execute(
        """
        UPDATE assays
        SET platform = CASE
            WHEN assay_type IS NULL OR BTRIM(assay_type) = '' THEN 'metabolomics'
            WHEN LOWER(assay_type) LIKE 'metabolomics%' THEN 'metabolomics'
            ELSE LOWER(assay_type)
        END
        """
    )
    op.alter_column("assays", "platform", existing_type=sa.String(length=100), nullable=False, server_default="metabolomics")

    op.add_column("metabolomics_assays", sa.Column("assay_type", sa.String(length=50), nullable=True, server_default="LC_MS"))
    op.add_column("metabolomics_assays", sa.Column("analysis_scope", sa.String(length=255), nullable=True))
    op.add_column("metabolomics_assays", sa.Column("analyte_domain", sa.String(length=255), nullable=True))
    op.execute(
        """
        UPDATE metabolomics_assays AS ma
        SET assay_type = CASE
            WHEN LOWER(a.assay_type) = 'metabolomics_gcms' THEN 'GC_MS'
            WHEN LOWER(a.assay_type) = 'metabolomics_nmr' THEN 'NMR'
            ELSE 'LC_MS'
        END
        FROM assays AS a
        WHERE a.id = ma.assay_id
        """
    )
    op.alter_column("metabolomics_assays", "assay_type", existing_type=sa.String(length=50), nullable=False, server_default="LC_MS")

    op.create_table(
        "lcms_assay_details",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("metabolomics_assay_id", sa.Integer(), nullable=False),
        sa.Column("polarity", sa.String(length=100), nullable=True),
        sa.Column("column_model", sa.String(length=255), nullable=True),
        sa.Column("column_type", sa.String(length=255), nullable=True),
        sa.Column("column_guard_type", sa.String(length=255), nullable=True),
        sa.Column("chromatography_instrument", sa.String(length=255), nullable=True),
        sa.Column("scan_mz_range", sa.String(length=100), nullable=True),
        sa.Column("ion_source", sa.String(length=255), nullable=True),
        sa.Column("mass_analyzer", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["metabolomics_assay_id"], ["metabolomics_assays.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("metabolomics_assay_id"),
    )
    op.create_index(op.f("ix_lcms_assay_details_metabolomics_assay_id"), "lcms_assay_details", ["metabolomics_assay_id"], unique=True)

    op.create_table(
        "gcms_assay_details",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("metabolomics_assay_id", sa.Integer(), nullable=False),
        sa.Column("derivatization", sa.String(length=255), nullable=True),
        sa.Column("chromatography_instrument", sa.String(length=255), nullable=True),
        sa.Column("column_model", sa.String(length=255), nullable=True),
        sa.Column("column_type", sa.String(length=255), nullable=True),
        sa.Column("column_guard_type", sa.String(length=255), nullable=True),
        sa.Column("scan_mz_range", sa.String(length=100), nullable=True),
        sa.Column("ion_source", sa.String(length=255), nullable=True),
        sa.Column("mass_analyzer", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["metabolomics_assay_id"], ["metabolomics_assays.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("metabolomics_assay_id"),
    )
    op.create_index(op.f("ix_gcms_assay_details_metabolomics_assay_id"), "gcms_assay_details", ["metabolomics_assay_id"], unique=True)

    op.create_table(
        "nmr_assay_details",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("metabolomics_assay_id", sa.Integer(), nullable=False),
        sa.Column("nmr_tube_type", sa.String(length=255), nullable=True),
        sa.Column("solvent", sa.String(length=255), nullable=True),
        sa.Column("sample_ph", sa.String(length=100), nullable=True),
        sa.Column("temperature", sa.String(length=100), nullable=True),
        sa.Column("nmr_probe", sa.String(length=255), nullable=True),
        sa.Column("number_of_transients", sa.String(length=100), nullable=True),
        sa.Column("pulse_sequence_name", sa.String(length=255), nullable=True),
        sa.Column("magnetic_field_strength", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["metabolomics_assay_id"], ["metabolomics_assays.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("metabolomics_assay_id"),
    )
    op.create_index(op.f("ix_nmr_assay_details_metabolomics_assay_id"), "nmr_assay_details", ["metabolomics_assay_id"], unique=True)

    op.execute(
        """
        INSERT INTO lcms_assay_details (
            metabolomics_assay_id,
            polarity,
            column_model,
            column_type,
            chromatography_instrument,
            scan_mz_range,
            ion_source,
            mass_analyzer
        )
        SELECT
            id,
            polarity,
            column_model,
            column_type,
            chromatography_instrument,
            scan_mz_range,
            ion_source,
            mass_analyzer
        FROM metabolomics_assays
        WHERE assay_type = 'LC_MS'
        """
    )

    op.drop_column("metabolomics_assays", "polarity")
    op.drop_column("metabolomics_assays", "column_type")
    op.drop_column("metabolomics_assays", "chromatography_instrument")
    op.drop_column("metabolomics_assays", "column_model")
    op.drop_column("metabolomics_assays", "scan_mz_range")
    op.drop_column("metabolomics_assays", "ion_source")
    op.drop_column("metabolomics_assays", "mass_analyzer")

    op.drop_column("assays", "assay_type")


def downgrade() -> None:
    op.add_column("assays", sa.Column("assay_type", sa.String(length=100), nullable=True, server_default="metabolomics_lcms"))
    op.execute(
        """
        UPDATE assays AS a
        SET assay_type = CASE
            WHEN LOWER(a.platform) <> 'metabolomics' THEN a.platform
            WHEN ma.assay_type = 'GC_MS' THEN 'metabolomics_gcms'
            WHEN ma.assay_type = 'NMR' THEN 'metabolomics_nmr'
            ELSE 'metabolomics_lcms'
        END
        FROM metabolomics_assays AS ma
        WHERE ma.assay_id = a.id
        """
    )
    op.execute("UPDATE assays SET assay_type = COALESCE(assay_type, 'metabolomics_lcms')")
    op.alter_column("assays", "assay_type", existing_type=sa.String(length=100), nullable=False, server_default="metabolomics_lcms")

    op.add_column("metabolomics_assays", sa.Column("mass_analyzer", sa.String(length=255), nullable=True))
    op.add_column("metabolomics_assays", sa.Column("ion_source", sa.String(length=255), nullable=True))
    op.add_column("metabolomics_assays", sa.Column("scan_mz_range", sa.String(length=100), nullable=True))
    op.add_column("metabolomics_assays", sa.Column("column_model", sa.String(length=255), nullable=True))
    op.add_column("metabolomics_assays", sa.Column("chromatography_instrument", sa.String(length=255), nullable=True))
    op.add_column("metabolomics_assays", sa.Column("column_type", sa.String(length=255), nullable=True))
    op.add_column("metabolomics_assays", sa.Column("polarity", sa.String(length=100), nullable=True))

    op.execute(
        """
        UPDATE metabolomics_assays AS ma
        SET
            polarity = ld.polarity,
            column_model = ld.column_model,
            column_type = ld.column_type,
            chromatography_instrument = ld.chromatography_instrument,
            scan_mz_range = ld.scan_mz_range,
            ion_source = ld.ion_source,
            mass_analyzer = ld.mass_analyzer
        FROM lcms_assay_details AS ld
        WHERE ld.metabolomics_assay_id = ma.id
        """
    )
    op.execute(
        """
        UPDATE metabolomics_assays AS ma
        SET
            column_model = COALESCE(ma.column_model, gd.column_model),
            column_type = COALESCE(ma.column_type, gd.column_type),
            chromatography_instrument = COALESCE(ma.chromatography_instrument, gd.chromatography_instrument),
            scan_mz_range = COALESCE(ma.scan_mz_range, gd.scan_mz_range),
            ion_source = COALESCE(ma.ion_source, gd.ion_source),
            mass_analyzer = COALESCE(ma.mass_analyzer, gd.mass_analyzer)
        FROM gcms_assay_details AS gd
        WHERE gd.metabolomics_assay_id = ma.id
        """
    )

    op.drop_index(op.f("ix_nmr_assay_details_metabolomics_assay_id"), table_name="nmr_assay_details")
    op.drop_table("nmr_assay_details")
    op.drop_index(op.f("ix_gcms_assay_details_metabolomics_assay_id"), table_name="gcms_assay_details")
    op.drop_table("gcms_assay_details")
    op.drop_index(op.f("ix_lcms_assay_details_metabolomics_assay_id"), table_name="lcms_assay_details")
    op.drop_table("lcms_assay_details")

    op.drop_column("metabolomics_assays", "analyte_domain")
    op.drop_column("metabolomics_assays", "analysis_scope")
    op.drop_column("metabolomics_assays", "assay_type")

    op.drop_column("assays", "platform")
