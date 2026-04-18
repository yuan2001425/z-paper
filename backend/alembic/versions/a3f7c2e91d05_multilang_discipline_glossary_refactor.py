"""multilang_discipline_glossary_refactor

Revision ID: a3f7c2e91d05
Revises: b215911d89aa
Create Date: 2026-04-06 12:00:00.000000

Changes:
- papers: add source_language, add domain, expand division to VARCHAR(500)
- user_glossaries: rename en_term -> foreign_term, add source_language, add domain,
                   update unique constraint
- new table: user_translation_preferences
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3f7c2e91d05"
down_revision: Union[str, None] = "b215911d89aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── papers 表 ─────────────────────────────────────────────────────────────
    op.add_column("papers", sa.Column(
        "source_language", sa.String(20), nullable=False, server_default="en"
    ))
    op.add_column("papers", sa.Column(
        "domain", sa.String(100), nullable=True
    ))
    op.alter_column("papers", "division",
        existing_type=sa.String(100),
        type_=sa.String(500),
        existing_nullable=True,
    )

    # ── user_glossaries 表 ────────────────────────────────────────────────────
    # 1. 删除旧唯一约束
    op.drop_constraint("uq_user_term", "user_glossaries", type_="unique")

    # 2. 重命名列 en_term -> foreign_term
    op.alter_column("user_glossaries", "en_term",
        new_column_name="foreign_term",
        existing_type=sa.String(300),
        existing_nullable=False,
    )

    # 3. 添加新列
    op.add_column("user_glossaries", sa.Column(
        "source_language", sa.String(20), nullable=False, server_default="en"
    ))
    op.add_column("user_glossaries", sa.Column(
        "domain", sa.String(100), nullable=True
    ))

    # 4. 建立新唯一约束
    op.create_unique_constraint(
        "uq_user_term_lang",
        "user_glossaries",
        ["user_id", "foreign_term", "source_language"],
    )

    # ── 新表 user_translation_preferences ────────────────────────────────────
    op.create_table(
        "user_translation_preferences",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("action", sa.String(20), nullable=False, server_default="never_translate"),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "category", name="uq_user_pref_category"),
    )
    op.create_index("ix_user_translation_preferences_user_id",
                    "user_translation_preferences", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_translation_preferences")

    op.drop_constraint("uq_user_term_lang", "user_glossaries", type_="unique")
    op.drop_column("user_glossaries", "domain")
    op.drop_column("user_glossaries", "source_language")
    op.alter_column("user_glossaries", "foreign_term",
        new_column_name="en_term",
        existing_type=sa.String(300),
        existing_nullable=False,
    )
    op.create_unique_constraint("uq_user_term", "user_glossaries", ["user_id", "en_term"])

    op.alter_column("papers", "division",
        existing_type=sa.String(500),
        type_=sa.String(100),
        existing_nullable=True,
    )
    op.drop_column("papers", "domain")
    op.drop_column("papers", "source_language")
