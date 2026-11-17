"""Mocked tests for centralized Gemini and Groq client setup and handling."""

import asyncio
import io
import logging
import sys
import types as pytypes
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic import BaseModel, Field, SecretStr

from app.core.config import Settings
from app.services.career_enrichment import RoadmapEnrichment, _GroqRoadmapResponse
from app.services.llm_providers import (
    CLEANUP_TIMEOUT_SECONDS,
    LLMConfigurationError,
    LLMInvalidResponseError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMProviderFailureError,
    LLMProviderService,
    _gemini_compatible_json_schema,
)


def run_without_event_loop(awaitable):
    """Drive immediately-resolving mocked coroutines without Windows loop setup."""
    try:
        awaitable.send(None)
    except StopIteration as completed:
        return completed.value
    except StopAsyncIteration:
        return None
    raise AssertionError("A mocked provider coroutine unexpectedly performed I/O.")


async def await_immediately(awaitable, timeout):
    return await awaitable


class ScheduledCleanup:
    """Small task stand-in for deterministic cleanup timeout tests."""

    def __init__(self, coroutine):
        self.coroutine = coroutine
        self.finished = False
        self.cancelled = False
        self.value = None
        self.error = None
        try:
            coroutine.send(None)
        except StopIteration as result:
            self.finished = True
            self.value = result.value
        except BaseException as error:
            self.finished = True
            self.error = error

    def done(self):
        return self.finished

    def result(self):
        if self.error is not None:
            raise self.error
        return self.value

    def cancel(self):
        self.cancelled = True
        try:
            self.coroutine.close()
        except BaseException:
            pass

    def add_done_callback(self, callback):
        # Awaiting an immediate fake may finish at cancellation time as well.
        if self.finished:
            callback(self)


def run_with_cleanup_scheduler(awaitable):
    tasks = []
    observed_timeouts = []

    def create_task(coroutine):
        task = ScheduledCleanup(coroutine)
        tasks.append(task)
        return task

    async def wait(task_set, timeout):
        observed_timeouts.append(timeout)
        done = {task for task in task_set if task.done()}
        return done, set(task_set) - done

    async def to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    with patch("app.services.llm_providers.asyncio.create_task", side_effect=create_task), \
         patch("app.services.llm_providers.asyncio.wait", side_effect=wait), \
         patch("app.services.llm_providers.asyncio.to_thread", side_effect=to_thread):
        run_without_event_loop(awaitable)
    return tasks, observed_timeouts


class SuspendedAwaitable:
    def __await__(self):
        yield "pending cleanup"


class ExampleResponse(BaseModel):
    explanation: str


class BoundedExampleResponse(BaseModel):
    explanation: str = Field(min_length=5, max_length=20)


class FakeProviderStatusError(Exception):
    def __init__(self, status_code):
        super().__init__("mock provider error")
        self.status_code = status_code


class FakeGeminiClient:
    def __init__(self, response=None, side_effect=None):
        self.generate_content = AsyncMock(return_value=response, side_effect=side_effect)
        self.aio = SimpleNamespace(
            models=SimpleNamespace(generate_content=self.generate_content),
            aclose=AsyncMock(),
        )
        self.close = Mock()


class FakeGroqClient:
    def __init__(self, response=None, side_effect=None):
        self.create = AsyncMock(return_value=response, side_effect=side_effect)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.close_mock = AsyncMock()

    async def close(self):
        await self.close_mock()


def fake_settings(*, gemini=None, groq=None, timeout=1.0, roadmap_max_output_tokens=4096):
    return SimpleNamespace(
        gemini_api_key=SecretStr(gemini) if gemini is not None else None,
        groq_api_key=SecretStr(groq) if groq is not None else None,
        gemini_model_id="gemini-3.8-flash",
        groq_model_id="openai/gpt-oss-120b",
        llm_provider_timeout_seconds=timeout,
        llm_roadmap_max_output_tokens=roadmap_max_output_tokens,
    )


class LLMProviderTests(unittest.TestCase):
    def test_provider_settings_are_optional_secret_strings_with_approved_model_defaults(self):
        defaults = Settings(_env_file=None)
        self.assertIsNone(defaults.gemini_api_key)
        self.assertIsNone(defaults.groq_api_key)
        self.assertEqual(defaults.gemini_model_id, "gemini-3.8-flash")
        self.assertEqual(defaults.groq_model_id, "openai/gpt-oss-120b")
        self.assertEqual(defaults.llm_roadmap_max_output_tokens, 4096)

        configured = Settings(
            _env_file=None,
            gemini_api_key="mock-gemini-key",
            groq_api_key="mock-groq-key",
            llm_roadmap_max_output_tokens=2048,
        )
        self.assertIsInstance(configured.gemini_api_key, SecretStr)
        self.assertIsInstance(configured.groq_api_key, SecretStr)
        self.assertEqual(configured.llm_roadmap_max_output_tokens, 2048)

    def test_both_keys_missing_creates_no_clients(self):
        gemini_factory = Mock()
        groq_factory = Mock()
        service = LLMProviderService(
            fake_settings(),
            gemini_client_factory=gemini_factory,
            groq_client_factory=groq_factory,
        )

        run_without_event_loop(service.startup())

        self.assertEqual(service.configured_providers, ())
        gemini_factory.assert_not_called()
        groq_factory.assert_not_called()
        with self.assertRaises(LLMConfigurationError):
            run_without_event_loop(service.generate_json("gemini", prompt="test", response_model=ExampleResponse))

    def test_only_gemini_configured(self):
        gemini_client = FakeGeminiClient()
        gemini_factory = Mock(return_value=gemini_client)
        groq_factory = Mock()
        service = LLMProviderService(
            fake_settings(gemini="mock-gemini-key"),
            gemini_client_factory=gemini_factory,
            groq_client_factory=groq_factory,
        )

        run_without_event_loop(service.startup())

        self.assertEqual(service.configured_providers, ("gemini",))
        gemini_factory.assert_called_once_with("mock-gemini-key", 1.0)
        groq_factory.assert_not_called()

    def test_only_groq_configured(self):
        groq_client = FakeGroqClient()
        gemini_factory = Mock()
        groq_factory = Mock(return_value=groq_client)
        service = LLMProviderService(
            fake_settings(groq="mock-groq-key"),
            gemini_client_factory=gemini_factory,
            groq_client_factory=groq_factory,
        )

        run_without_event_loop(service.startup())

        self.assertEqual(service.configured_providers, ("groq",))
        gemini_factory.assert_not_called()
        groq_factory.assert_called_once_with("mock-groq-key", 1.0)

    def test_one_client_initialization_failure_does_not_block_the_other_provider(self):
        groq_client = FakeGroqClient()
        service = LLMProviderService(
            fake_settings(gemini="mock-gemini-key", groq="mock-groq-key"),
            gemini_client_factory=Mock(side_effect=ImportError("mock missing SDK")),
            groq_client_factory=lambda _key, _timeout: groq_client,
        )

        run_without_event_loop(service.startup())

        self.assertEqual(service.configured_providers, ("gemini", "groq"))
        with self.assertRaises(LLMConfigurationError):
            service._client_for("gemini")
        self.assertIs(service._client_for("groq")[0], groq_client)

    def test_both_providers_initialize_without_network_calls_and_cleanup(self):
        gemini_client = FakeGeminiClient()
        groq_client = FakeGroqClient()
        network_calls = []

        def gemini_factory(_key, _timeout):
            return gemini_client

        def groq_factory(_key, _timeout):
            return groq_client

        service = LLMProviderService(
            fake_settings(gemini="mock-gemini-key", groq="mock-groq-key"),
            gemini_client_factory=gemini_factory,
            groq_client_factory=groq_factory,
        )
        run_without_event_loop(service.startup())
        self.assertEqual(service.configured_providers, ("gemini", "groq"))
        self.assertEqual(network_calls, [])

        run_with_cleanup_scheduler(service.shutdown())

        gemini_client.aio.aclose.assert_awaited_once()
        gemini_client.close.assert_called_once()
        groq_client.close_mock.assert_awaited_once()

    def test_gemini_cleanup_timeout_is_bounded_and_remaining_cleanup_continues(self):
        gemini_client = FakeGeminiClient()

        async def blocked_close():
            await SuspendedAwaitable()

        gemini_client.aio.aclose = Mock(side_effect=blocked_close)
        service = LLMProviderService(
            fake_settings(gemini="mock-key"),
            gemini_client_factory=lambda _key, _timeout: gemini_client,
        )
        run_without_event_loop(service.startup())

        tasks, timeouts = run_with_cleanup_scheduler(service.shutdown())

        self.assertEqual(timeouts, [CLEANUP_TIMEOUT_SECONDS, CLEANUP_TIMEOUT_SECONDS])
        self.assertEqual(len(tasks), 2)
        self.assertTrue(tasks[0].cancelled)
        self.assertTrue(tasks[1].finished)
        gemini_client.close.assert_called_once()
        self.assertIsNone(service._gemini_root_client)

    def test_gemini_cleanup_exception_does_not_prevent_groq_cleanup(self):
        gemini_client = FakeGeminiClient()
        gemini_client.aio.aclose = Mock(side_effect=RuntimeError("private async cleanup details"))
        gemini_client.close.side_effect = RuntimeError("private sync cleanup details")
        groq_client = FakeGroqClient()
        service = LLMProviderService(
            fake_settings(gemini="mock-key", groq="mock-key"),
            gemini_client_factory=lambda _key, _timeout: gemini_client,
            groq_client_factory=lambda _key, _timeout: groq_client,
        )
        run_without_event_loop(service.startup())

        tasks, _timeouts = run_with_cleanup_scheduler(service.shutdown())

        self.assertEqual(len(tasks), 2)
        self.assertTrue(all(task.finished for task in tasks))
        gemini_client.close.assert_called_once()
        groq_client.close_mock.assert_awaited_once()

    def test_generation_success_is_preserved_when_cleanup_fails(self):
        gemini_client = FakeGeminiClient(response=SimpleNamespace(text='{"explanation":"Grounded result"}'))
        gemini_client.aio.aclose = Mock(side_effect=RuntimeError("private async cleanup details"))
        gemini_client.close.side_effect = RuntimeError("private sync cleanup details")
        service = LLMProviderService(
            fake_settings(gemini="mock-key"),
            gemini_client_factory=lambda _key, _timeout: gemini_client,
        )
        fake_google = pytypes.ModuleType("google")
        fake_google.__path__ = []
        fake_genai = pytypes.ModuleType("google.genai")
        fake_genai.types = SimpleNamespace(GenerateContentConfig=lambda **kwargs: kwargs)

        with patch.dict(sys.modules, {"google": fake_google, "google.genai": fake_genai}):
            run_without_event_loop(service.startup())
            with patch("app.services.llm_providers.asyncio.wait_for", side_effect=await_immediately):
                result = run_without_event_loop(service.generate_json(
                    "gemini", prompt="mock prompt", response_model=ExampleResponse
                ))

        run_with_cleanup_scheduler(service.shutdown())
        self.assertEqual(result.explanation, "Grounded result")

    def test_career_enrichment_logger_uses_uvicorn_console_once_at_info(self):
        from app import main

        logger = logging.getLogger("app.services.career_enrichment")
        uvicorn_logger = logging.getLogger("uvicorn")
        uvicorn_error_logger = logging.getLogger("uvicorn.error")
        original_logger_state = (logger.handlers[:], logger.level, logger.propagate)
        original_uvicorn_handlers = uvicorn_logger.handlers[:]
        original_error_handlers = uvicorn_error_logger.handlers[:]
        output = io.StringIO()
        console_handler = logging.StreamHandler(output)
        try:
            logger.handlers.clear()
            uvicorn_logger.handlers[:] = [console_handler]
            uvicorn_error_logger.handlers.clear()

            main._configure_career_enrichment_logging()
            main._configure_career_enrichment_logging()
            logger.info("career_roadmap_ai.consent.passed")

            self.assertEqual(logger.level, logging.INFO)
            self.assertFalse(logger.propagate)
            self.assertEqual(logger.handlers, [console_handler])
            self.assertEqual(output.getvalue().count("career_roadmap_ai.consent.passed"), 1)
        finally:
            logger.handlers[:] = original_logger_state[0]
            logger.setLevel(original_logger_state[1])
            logger.propagate = original_logger_state[2]
            uvicorn_logger.handlers[:] = original_uvicorn_handlers
            uvicorn_error_logger.handlers[:] = original_error_handlers
            console_handler.close()

    def test_application_shutdown_continues_after_provider_cleanup_timeout(self):
        from app import main

        gemini_client = FakeGeminiClient()

        async def blocked_close():
            await SuspendedAwaitable()

        gemini_client.aio.aclose = Mock(side_effect=blocked_close)
        service = LLMProviderService(
            fake_settings(gemini="mock-key"),
            gemini_client_factory=lambda _key, _timeout: gemini_client,
        )
        run_without_event_loop(service.startup())
        rate_shutdown = AsyncMock()
        context = main.lifespan(object())

        with patch.object(main.rate_limit_service, "startup", new=AsyncMock()), \
             patch.object(main.rate_limit_service, "shutdown", new=rate_shutdown), \
             patch.object(main.llm_provider_service, "startup", new=AsyncMock()), \
             patch.object(main.llm_provider_service, "shutdown", side_effect=service.shutdown):
            run_without_event_loop(context.__aenter__())
            _tasks, timeouts = run_with_cleanup_scheduler(context.__aexit__(None, None, None))

        self.assertEqual(timeouts, [CLEANUP_TIMEOUT_SECONDS, CLEANUP_TIMEOUT_SECONDS])
        rate_shutdown.assert_awaited_once()

    def test_official_sdk_factories_receive_keys_timeouts_and_no_retry_configuration(self):
        gemini_client = FakeGeminiClient()
        groq_client = FakeGroqClient()
        google_module = pytypes.ModuleType("google")
        google_module.__path__ = []
        google_genai = pytypes.ModuleType("google.genai")
        options = SimpleNamespace(
            HttpRetryOptions=Mock(side_effect=lambda **kwargs: kwargs),
            HttpOptions=Mock(side_effect=lambda **kwargs: kwargs),
        )
        genai_client_factory = Mock(return_value=gemini_client)
        google_genai.Client = genai_client_factory
        google_genai.types = options
        groq_module = pytypes.ModuleType("groq")
        groq_client_factory = Mock(return_value=groq_client)
        groq_module.AsyncGroq = groq_client_factory
        service = LLMProviderService(fake_settings(gemini="g-key", groq="r-key", timeout=2.5))

        with patch.dict(sys.modules, {
            "google": google_module,
            "google.genai": google_genai,
            "groq": groq_module,
        }):
            run_without_event_loop(service.startup())

        retry_args = options.HttpRetryOptions.call_args.kwargs
        self.assertEqual(retry_args, {"attempts": 1})
        http_args = options.HttpOptions.call_args.kwargs
        self.assertEqual(http_args["timeout"], 2500)
        self.assertEqual(http_args["retry_options"], retry_args)
        genai_client_factory.assert_called_once()
        self.assertEqual(genai_client_factory.call_args.kwargs["api_key"], "g-key")
        groq_client_factory.assert_called_once_with(api_key="r-key", timeout=2.5, max_retries=0)
        self.assertEqual(gemini_client.generate_content.await_count, 0)
        self.assertEqual(groq_client.create.await_count, 0)

    def test_gemini_structured_response_is_parsed_and_validated(self):
        gemini_client = FakeGeminiClient(response=SimpleNamespace(text='{"explanation":"Grounded result"}'))
        service = LLMProviderService(
            fake_settings(gemini="mock-key"),
            gemini_client_factory=lambda _key, _timeout: gemini_client,
        )
        fake_google = pytypes.ModuleType("google")
        fake_google.__path__ = []
        fake_genai = pytypes.ModuleType("google.genai")
        fake_genai.types = SimpleNamespace(GenerateContentConfig=lambda **kwargs: kwargs)

        with patch.dict(sys.modules, {"google": fake_google, "google.genai": fake_genai}):
            run_without_event_loop(service.startup())
            with patch("app.services.llm_providers.asyncio.wait_for", side_effect=await_immediately):
                result = run_without_event_loop(service.generate_json(
                    "gemini", prompt="mock prompt", response_model=ExampleResponse,
                    max_output_tokens=2048,
                ))

        self.assertEqual(result.explanation, "Grounded result")
        kwargs = gemini_client.generate_content.await_args.kwargs
        self.assertEqual(kwargs["model"], "gemini-3.8-flash")
        self.assertEqual(kwargs["config"]["response_mime_type"], "application/json")
        self.assertEqual(kwargs["config"]["response_json_schema"]["properties"]["explanation"]["type"], "string")
        self.assertNotIn("response_schema", kwargs["config"])
        self.assertEqual(kwargs["config"]["max_output_tokens"], 2048)

    def test_gemini_roadmap_schema_removes_only_unsupported_string_bounds(self):
        original = RoadmapEnrichment.model_json_schema()
        compatible = _gemini_compatible_json_schema(original)

        def collect_keys(value):
            if isinstance(value, dict):
                return set(value).union(*(collect_keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(collect_keys(item) for item in value))
            return set()

        original_keys = collect_keys(original)
        compatible_keys = collect_keys(compatible)
        self.assertIn("minLength", original_keys)
        self.assertIn("maxLength", original_keys)
        self.assertNotIn("minLength", compatible_keys)
        self.assertNotIn("maxLength", compatible_keys)
        for preserved in ("$defs", "$ref", "required", "enum", "additionalProperties", "maxItems", "minItems"):
            self.assertIn(preserved, compatible_keys)
        self.assertEqual(original, RoadmapEnrichment.model_json_schema())

    def test_gemini_pydantic_validation_still_rejects_relaxed_provider_schema_output(self):
        for response_text, accepted in (
            ('{"explanation":"valid response"}', True),
            ('{"explanation":"bad"}', False),
        ):
            with self.subTest(accepted=accepted):
                gemini_client = FakeGeminiClient(response=SimpleNamespace(text=response_text))
                service = LLMProviderService(
                    fake_settings(gemini="mock-key"),
                    gemini_client_factory=lambda _key, _timeout: gemini_client,
                )
                fake_google = pytypes.ModuleType("google")
                fake_google.__path__ = []
                fake_genai = pytypes.ModuleType("google.genai")
                fake_genai.types = SimpleNamespace(GenerateContentConfig=lambda **kwargs: kwargs)

                with patch.dict(sys.modules, {"google": fake_google, "google.genai": fake_genai}):
                    run_without_event_loop(service.startup())
                    with patch("app.services.llm_providers.asyncio.wait_for", side_effect=await_immediately):
                        if accepted:
                            result = run_without_event_loop(service.generate_json(
                                "gemini", prompt="mock prompt", response_model=BoundedExampleResponse
                            ))
                            self.assertEqual(result.explanation, "valid response")
                        else:
                            with self.assertRaises(LLMInvalidResponseError):
                                run_without_event_loop(service.generate_json(
                                    "gemini", prompt="mock prompt", response_model=BoundedExampleResponse
                                ))

                sent_schema = gemini_client.generate_content.await_args.kwargs["config"]["response_json_schema"]
                self.assertNotIn("minLength", sent_schema["properties"]["explanation"])
                self.assertNotIn("maxLength", sent_schema["properties"]["explanation"])

    def test_groq_structured_response_is_parsed_and_validated(self):
        groq_client = FakeGroqClient(response=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"explanation":"Grounded result"}'))]
        ))
        service = LLMProviderService(
            fake_settings(groq="mock-key"),
            groq_client_factory=lambda _key, _timeout: groq_client,
        )
        run_without_event_loop(service.startup())
        with patch("app.services.llm_providers.asyncio.wait_for", side_effect=await_immediately):
            result = run_without_event_loop(service.generate_json(
                "groq", prompt="mock prompt", response_model=ExampleResponse,
                max_output_tokens=2048,
            ))

        self.assertEqual(result.explanation, "Grounded result")
        kwargs = groq_client.create.await_args.kwargs
        self.assertEqual(kwargs["model"], "openai/gpt-oss-120b")
        self.assertEqual(kwargs["response_format"]["type"], "json_schema")
        self.assertFalse(kwargs["response_format"]["json_schema"]["strict"])
        self.assertIn("properties", kwargs["response_format"]["json_schema"]["schema"])
        self.assertEqual(kwargs["max_completion_tokens"], 2048)

    def test_invalid_output_token_limits_are_rejected_before_dispatch(self):
        groq_client = FakeGroqClient()
        service = LLMProviderService(
            fake_settings(groq="mock-key"),
            groq_client_factory=lambda _key, _timeout: groq_client,
        )
        run_without_event_loop(service.startup())
        for invalid in (0, -1, True, "2048"):
            with self.subTest(invalid=invalid), self.assertRaises(LLMConfigurationError):
                run_without_event_loop(service.generate_json(
                    "groq", prompt="mock prompt", response_model=ExampleResponse,
                    max_output_tokens=invalid,
                ))
        groq_client.create.assert_not_awaited()

    def test_groq_transport_model_validation_exposes_only_safe_diagnostics(self):
        response_text = (
            '{"career_growth":[{"text":"The catalogue lists a possible direction.",'
            '"related_role_ids":[]}]}'
        )
        groq_client = FakeGroqClient(response=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response_text))]
        ))
        service = LLMProviderService(
            fake_settings(groq="mock-key"),
            groq_client_factory=lambda _key, _timeout: groq_client,
        )
        run_without_event_loop(service.startup())

        with patch("app.services.llm_providers.asyncio.wait_for", side_effect=await_immediately):
            with self.assertRaises(LLMInvalidResponseError) as raised:
                run_without_event_loop(service.generate_json(
                    "groq", prompt="synthetic prompt", response_model=_GroqRoadmapResponse
                ))

        error = raised.exception
        self.assertEqual(error.validation_boundary, "provider_response_model")
        self.assertEqual(error.field_path, "career_growth[].related_role_ids")
        self.assertEqual(error.validator_id, "collection_length_constraint")
        self.assertNotIn("possible direction", str(error))
        self.assertNotIn("related_role_ids", str(error))

    def test_groq_transport_model_rejects_learning_guidance_text_with_safe_diagnostics(self):
        prohibited_text = "This roadmap guarantees a job offer in six months."
        response_text = (
            '{"learning_guidance":[{"text":"This roadmap guarantees a job offer in six months.",'
            '"skill_ids":[]}]} '
        ).strip()
        groq_client = FakeGroqClient(response=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response_text))]
        ))
        service = LLMProviderService(
            fake_settings(groq="mock-key"),
            groq_client_factory=lambda _key, _timeout: groq_client,
        )
        run_without_event_loop(service.startup())

        with patch("app.services.llm_providers.asyncio.wait_for", side_effect=await_immediately):
            with self.assertRaises(LLMInvalidResponseError) as raised:
                run_without_event_loop(service.generate_json(
                    "groq", prompt="synthetic prompt", response_model=_GroqRoadmapResponse
                ))

        error = raised.exception
        self.assertEqual(error.validation_boundary, "provider_response_model")
        self.assertEqual(error.field_path, "learning_guidance[].text")
        self.assertEqual(error.validator_id, "custom_validator")
        self.assertNotIn(prohibited_text, str(error))
        self.assertNotIn("guarantees a job offer", str(error))

    def test_transport_validation_diagnostics_hide_unexpected_field_names(self):
        response_text = '{"learning_guidance":[],"PRIVATE_GENERATED_FIELD":"private"}'
        groq_client = FakeGroqClient(response=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response_text))]
        ))
        service = LLMProviderService(
            fake_settings(groq="mock-key"),
            groq_client_factory=lambda _key, _timeout: groq_client,
        )
        run_without_event_loop(service.startup())

        with patch("app.services.llm_providers.asyncio.wait_for", side_effect=await_immediately):
            with self.assertRaises(LLMInvalidResponseError) as raised:
                run_without_event_loop(service.generate_json(
                    "groq", prompt="synthetic prompt", response_model=_GroqRoadmapResponse
                ))

        self.assertEqual(raised.exception.field_path, "unclassified")
        self.assertEqual(raised.exception.validator_id, "unexpected_field")
        self.assertNotIn("PRIVATE_GENERATED_FIELD", str(raised.exception))
        self.assertNotIn("private", str(raised.exception))

    def test_rate_limit_and_timeout_errors_are_normalized(self):
        groq_client = FakeGroqClient(side_effect=FakeProviderStatusError(429))
        service = LLMProviderService(
            fake_settings(groq="mock-key"),
            groq_client_factory=lambda _key, _timeout: groq_client,
        )
        run_without_event_loop(service.startup())
        with patch("app.services.llm_providers.asyncio.wait_for", side_effect=await_immediately):
            with self.assertRaises(LLMRateLimitError) as rate_limit:
                run_without_event_loop(service.generate_json("groq", prompt="mock", response_model=ExampleResponse))
        self.assertEqual(rate_limit.exception.diagnostic_category, "rate_limit")
        self.assertEqual(rate_limit.exception.http_status, 429)
        self.assertEqual(rate_limit.exception.sdk_exception_category, "rate_limit_exception")

        slow_gemini = FakeGeminiClient()
        gemini_service = LLMProviderService(
            fake_settings(gemini="mock-key", timeout=0.01),
            gemini_client_factory=lambda _key, _timeout: slow_gemini,
        )
        fake_google = pytypes.ModuleType("google")
        fake_google.__path__ = []
        fake_genai = pytypes.ModuleType("google.genai")
        fake_genai.types = SimpleNamespace(GenerateContentConfig=lambda **kwargs: kwargs)
        def timeout_immediately(awaitable, timeout):
            awaitable.close()
            raise asyncio.TimeoutError()

        with patch.dict(sys.modules, {"google": fake_google, "google.genai": fake_genai}):
            run_without_event_loop(gemini_service.startup())
            with patch("app.services.llm_providers.asyncio.wait_for", side_effect=timeout_immediately):
                with self.assertRaises(LLMTimeoutError) as timed_out:
                    run_without_event_loop(gemini_service.generate_json("gemini", prompt="mock", response_model=ExampleResponse))
        self.assertEqual(timed_out.exception.diagnostic_category, "timeout")

    def test_authentication_network_and_provider_failures_are_normalized(self):
        cases = (
            (FakeProviderStatusError(401), LLMConfigurationError, "configuration/authentication"),
            (ConnectionError("mock network error"), LLMNetworkError, "network"),
            (FakeProviderStatusError(503), LLMProviderFailureError, "provider_failure"),
        )
        for error, expected_error, expected_category in cases:
            with self.subTest(expected_error=expected_error.__name__):
                client = FakeGroqClient(side_effect=error)
                service = LLMProviderService(
                    fake_settings(groq="mock-key"),
                    groq_client_factory=lambda _key, _timeout: client,
                )
                run_without_event_loop(service.startup())
                with patch("app.services.llm_providers.asyncio.wait_for", side_effect=await_immediately):
                    with self.assertRaises(expected_error) as raised:
                        run_without_event_loop(service.generate_json("groq", prompt="mock", response_model=ExampleResponse))
                self.assertEqual(raised.exception.diagnostic_category, expected_category)

    def test_unclassified_http_errors_keep_only_safe_status_and_sdk_category(self):
        for status, sdk_category in ((400, "http_client_exception"), (404, "http_client_exception"), (503, "http_server_exception")):
            with self.subTest(status=status):
                client = FakeGroqClient(side_effect=FakeProviderStatusError(status))
                service = LLMProviderService(
                    fake_settings(groq="mock-key"),
                    groq_client_factory=lambda _key, _timeout: client,
                )
                run_without_event_loop(service.startup())
                with patch("app.services.llm_providers.asyncio.wait_for", side_effect=await_immediately):
                    with self.assertRaises(LLMProviderFailureError) as raised:
                        run_without_event_loop(service.generate_json(
                            "groq", prompt="synthetic", response_model=ExampleResponse
                        ))
                self.assertEqual(raised.exception.http_status, status)
                self.assertEqual(raised.exception.sdk_exception_category, sdk_category)
                self.assertNotIn("mock provider error", str(raised.exception))

    def test_malformed_and_schema_invalid_outputs_are_rejected(self):
        for output in ("not json", '{"other":"value"}'):
            with self.subTest(output=output):
                client = FakeGroqClient(response=SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=output))]
                ))
                service = LLMProviderService(
                    fake_settings(groq="mock-key"),
                    groq_client_factory=lambda _key, _timeout: client,
                )
                run_without_event_loop(service.startup())
                with patch("app.services.llm_providers.asyncio.wait_for", side_effect=await_immediately):
                    with self.assertRaises(LLMInvalidResponseError):
                        run_without_event_loop(service.generate_json("groq", prompt="mock", response_model=ExampleResponse))

        malformed_client = FakeGroqClient(response=SimpleNamespace(choices=[]))
        malformed_service = LLMProviderService(
            fake_settings(groq="mock-key"),
            groq_client_factory=lambda _key, _timeout: malformed_client,
        )
        run_without_event_loop(malformed_service.startup())
        with patch("app.services.llm_providers.asyncio.wait_for", side_effect=await_immediately):
            with self.assertRaises(LLMInvalidResponseError):
                run_without_event_loop(malformed_service.generate_json(
                    "groq", prompt="mock", response_model=ExampleResponse
                ))


if __name__ == "__main__":
    unittest.main()
