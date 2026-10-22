STUDY_STATUSES = {
    "draft",
    "submitted",
    "under_review",
    "approved",
    "processing",
    "completed",
    "archived",
}

STUDY_ALLOWED_TRANSITIONS = {
    "draft": {"submitted"},
    "submitted": {"under_review"},
    "under_review": {"approved"},
    "approved": {"processing"},
    "processing": {"completed"},
    "completed": {"archived"},
    "archived": set(),
}

# Workflow jobs that ran to an end, successfully or not. Only these can be
# continued, retried, soft-deleted or purged.
FINISHED_JOB_STATES = ("succeeded", "failed")

# Registered acquisition files a workflow may read (never failed or deleted ones).
ACCEPTED_INPUT_FILE_STATES = ("registered", "uploaded", "validated")

# Study package revisions still being built, and those that produced a folder
# whether or not MetaboLights validation passed.
PACKAGE_PENDING_STATES = ("queued", "building")
PACKAGE_BUILT_STATES = ("complete", "validation_failed")
