from __future__ import annotations

import importlib
import os
import sys
import unittest
from unittest.mock import Mock, patch


MODULE_NAME = "backend_api.services.legal_fulltext_mcp_client"


def _load_client(**env: str):
    with patch.dict(os.environ, env, clear=False):
        sys.modules.pop(MODULE_NAME, None)
        return importlib.import_module(MODULE_NAME)


class LegalFulltextMcpClientTimeoutTests(unittest.TestCase):
    def test_documented_client_timeout_is_a_floor_for_mode_timeouts(self) -> None:
        client = _load_client(
            ENNOSCHOLAR_LEGAL_MCP_CLIENT_TIMEOUT_SECONDS="75",
            ENNOSCHOLAR_LEGAL_MCP_TARGETED_TIMEOUT_SECONDS="25",
            ENNOSCHOLAR_LEGAL_MCP_BROAD_TIMEOUT_SECONDS="25",
        )

        self.assertEqual(client.TARGETED_TIMEOUT_SECONDS, 75.0)
        self.assertEqual(client.BROAD_TIMEOUT_SECONDS, 75.0)

    def test_timeout_is_reported_as_timeout_not_server_unavailable(self) -> None:
        client = _load_client(
            ENNOSCHOLAR_LEGAL_MCP_CLIENT_TIMEOUT_SECONDS="75",
            ENNOSCHOLAR_LEGAL_MCP_TARGETED_TIMEOUT_SECONDS="25",
            ENNOSCHOLAR_LEGAL_MCP_BROAD_TIMEOUT_SECONDS="25",
            ENNOSCHOLAR_LEGAL_MCP_CLIENT_MAX_RETRIES="1",
        )

        with patch.object(
            client._SESSION,
            "post",
            Mock(side_effect=client.requests.Timeout("read timed out")),
        ) as post:
            result = client.resolve_article_fulltext(title="Article test")

        self.assertEqual(post.call_args.kwargs["timeout"], (3.0, 75.0))
        self.assertEqual(result["status"], "mcp_timeout")
        self.assertTrue(result["retry_recommended"])


class LegalFulltextProviderAggregationTests(unittest.TestCase):
    def test_one_transient_provider_does_not_invalidate_completed_searches(self) -> None:
        from mcp_servers.legal_fulltext_mcp.domain.models import ProviderAttempt
        from mcp_servers.legal_fulltext_mcp.services.resolver import LegalFulltextResolver

        attempts = [
            ProviderAttempt(
                provider="hal",
                ok=True,
                status="searched_no_verified_candidate",
            ),
            ProviderAttempt(
                provider="arxiv",
                ok=False,
                status="provider_temporarily_unavailable",
                transient=True,
            ),
            ProviderAttempt(
                provider="zenodo",
                ok=True,
                status="searched_no_verified_candidate",
            ),
        ]

        code, _ = LegalFulltextResolver._failure(
            [],
            attempts,
            transient_failure=False,
        )

        self.assertEqual(code, "legal_copy_not_found")

    def test_all_useful_providers_unavailable_remains_retryable(self) -> None:
        from mcp_servers.legal_fulltext_mcp.domain.models import ProviderAttempt
        from mcp_servers.legal_fulltext_mcp.services.resolver import LegalFulltextResolver

        attempts = [
            ProviderAttempt(
                provider="arxiv",
                ok=False,
                status="provider_temporarily_unavailable",
                transient=True,
            )
        ]

        code, _ = LegalFulltextResolver._failure(
            [],
            attempts,
            transient_failure=True,
        )

        self.assertEqual(code, "provider_temporarily_unavailable")


if __name__ == "__main__":
    unittest.main()
