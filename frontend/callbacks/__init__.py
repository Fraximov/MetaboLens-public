from callbacks import feature_review, auth, cache_invalidation, dashboard, files, job_workspace, metadata, navigation, studies, summary, workflows
from callbacks import study_packaging


def register_all_callbacks(app):
    navigation.register_callbacks(app)
    job_workspace.register_callbacks(app)
    feature_review.register_callbacks(app)
    auth.register_callbacks(app)
    dashboard.register_callbacks(app)
    metadata.register_callbacks(app)
    studies.register_callbacks(app)
    study_packaging.register_callbacks(app)
    files.register_callbacks(app)
    cache_invalidation.register_callbacks(app)
    summary.register_callbacks(app)
    workflows.register_callbacks(app)
