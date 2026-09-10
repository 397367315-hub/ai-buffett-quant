from pathlib import Path
import re


WORKFLOW_ROOT = Path(__file__).resolve().parents[2] / ".github" / "workflows"
WORKFLOWS = (
    WORKFLOW_ROOT / "overnight-strategy.yml",
    WORKFLOW_ROOT / "daily-market-cache.yml",
)


def _post_blocks(workflow: str) -> list[str]:
    """Return shell blocks containing each curl POST request."""
    return re.findall(
        r"(?ms)(?P<block>^\s*response=\"\$\(curl.*?--request POST.*?\n(?:.*?\n)*?\s*\"\$BACKEND_URL/[^\"\s]+\")",
        workflow,
    )


def test_workflows_login_with_admin_secrets_and_export_masked_token():
    for path in WORKFLOWS:
        workflow = path.read_text(encoding="utf-8")

        assert "/api/v1/auth/login" in workflow, path
        assert "secrets.ADMIN_USERNAME" in workflow, path
        assert "secrets.ADMIN_PASSWORD" in workflow, path
        assert '"$GITHUB_OUTPUT"' in workflow, path
        assert 'echo "::add-mask::$token"' in workflow, path
        assert "id: admin_login" in workflow, path
        assert "jq -e '.code == 0" in workflow, path


def test_every_business_post_has_bearer_and_gets_remain_unauthenticated():
    expected_mutations = {
        "overnight-strategy.yml": "/api/v1/quant/overnight/runs",
        "daily-market-cache.yml": "/api/v1/data/sync",
    }

    for path in WORKFLOWS:
        workflow = path.read_text(encoding="utf-8")
        posts = _post_blocks(workflow)
        assert posts, path

        login_posts = [block for block in posts if "/api/v1/auth/login" in block]
        mutation_posts = [block for block in posts if "/api/v1/auth/login" not in block]
        assert len(login_posts) == 1, path
        assert len(mutation_posts) == 1, path
        assert expected_mutations[path.name] in mutation_posts[0], path
        assert '--header "Authorization: Bearer $ADMIN_TOKEN"' in mutation_posts[0], path

        get_lines = [line for line in workflow.splitlines() if '"$BACKEND_URL/' in line and "curl" not in line]
        assert get_lines, path
        for line in get_lines:
            assert "Authorization: Bearer" not in line, (path, line)
