"""Register Files page callbacks through focused workflow modules.

The Files page has several independent UI workflows. Keeping this facade means
`callbacks.__init__` still imports one `files` module, while the callback logic
is split into smaller files that match what a user is doing on the page.
"""

from callbacks import (
    files_acquisitions,
    files_folder_browser,
    files_registration,
    files_sample_metadata,
    files_workflow_inputs,
)


def register_callbacks(app):
    """Register all Files page callbacks without changing the public entrypoint."""

    files_acquisitions.register_callbacks(app)
    files_folder_browser.register_callbacks(app)
    files_sample_metadata.register_callbacks(app)
    files_registration.register_callbacks(app)
    files_workflow_inputs.register_callbacks(app)
