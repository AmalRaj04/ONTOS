from src.ingest.adapters.confluence import ConfluenceAdapter
from src.ingest.adapters.fireflies import FirefliesAdapter
from src.ingest.adapters.gdrive import GDriveAdapter
from src.ingest.adapters.github import GithubAdapter
from src.ingest.adapters.gmail import GmailAdapter
from src.ingest.adapters.hubspot import HubspotAdapter
from src.ingest.adapters.jira import JiraAdapter
from src.ingest.adapters.linear import LinearAdapter
from src.ingest.adapters.slack import SlackAdapter

ALL_ADAPTERS = {
    "confluence": ConfluenceAdapter,
    "fireflies": FirefliesAdapter,
    "gdrive": GDriveAdapter,
    "github": GithubAdapter,
    "gmail": GmailAdapter,
    "hubspot": HubspotAdapter,
    "jira": JiraAdapter,
    "linear": LinearAdapter,
    "slack": SlackAdapter,
}
