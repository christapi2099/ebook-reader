"""Local drop-in client for the Kokoro Modal GPU app.

``ModalKokoroClient`` is call-compatible with ``kokoro.KPipeline``: it is called
as ``client(text, voice=..., speed=...)`` and yields objects that unpack as
``(graphemes, phonemes, audio_ndarray)`` with float32 mono audio at 24000 Hz, so
``services/tts_engine.py`` consumes it without a single change.

Everything that can fail (no credentials, cold start over the timeout, network
error, Modal API error, malformed payload, empty audio) raises
``RemoteSynthesisError``.  Returning an empty iterable is never an option: the
reader would treat it as a successful synthesis of silence.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

import numpy as np

from services import env_config

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
DEFAULT_VOICE = "af_heart"
DEFAULT_LANG_CODE = "a"

DEFAULT_APP_NAME = "kokoro-tts"
DEFAULT_FUNCTION_NAME = "synthesize"
DEFAULT_EXPORT_FUNCTION_NAME = "synthesize_batch"
DEFAULT_TRANSPORT = "auto"
VALID_TRANSPORTS = ("auto", "sdk", "http")

# One short sentence whose only purpose is to make Modal start a GPU container.
WARMUP_TEXT = "Warm-up."

# How long a Modal container stays alive after its last call; matches
# MODAL_KOKORO_IDLE_SECONDS on the deployment. Within this window a container is
# assumed warm, so the reader does not announce a warm-up that is not happening.
# Read through env_config, not float(): this runs at import time, so an
# unparsable value used to stop the app from importing at all. Zero is a typo —
# it would mean "no container is ever warm", announcing a warm-up on every play.
DEFAULT_WARM_WINDOW_SECONDS = 60.0
WARM_WINDOW_SECONDS = env_config.positive_seconds(
    "MODAL_KOKORO_IDLE_SECONDS", DEFAULT_WARM_WINDOW_SECONDS
)

# A first call has to cold-start a GPU container (image pull + 327 MB model
# load), which is legitimately slow; a warm call answers in well under a second.
DEFAULT_TIMEOUT_SECONDS = 300.0

# How long a reachability probe result stays fresh, so /capabilities cannot be
# used to hammer the Modal API.
PROBE_TTL_SECONDS = 30.0


class RemoteSynthesisError(RuntimeError):
    """Raised when a remote synthesis cannot produce audio."""


# --------------------------------------------------------------------------
# Wire types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class KokoroToken:
    """Word-level timing, shaped like ``kokoro``'s own token objects.

    ``tts_engine`` reads ``token.text`` / ``token.phonemes`` / ``token.start_ts``
    / ``token.end_ts`` by attribute, so plain dicts from the wire format must be
    rehydrated into objects before they are handed over.
    """

    text: str
    phonemes: str = ""
    start_ts: float = 0.0
    end_ts: float = 0.0


@dataclass(frozen=True)
class BatchAudio:
    """One finished export batch: its audio plus each sentence's sample count.

    ``sentence_samples`` is what lets the exporter place chapter marks from real
    offsets instead of estimating them from word counts.
    """

    batch_index: int
    audio: np.ndarray
    sentence_samples: list[int] = field(default_factory=list)


class KokoroChunk:
    """One synthesis result: ``(graphemes, phonemes, audio_ndarray)``.

    Indexable and iterable like the named tuple ``tts_engine`` already unpacks,
    so ``result[-1]`` is the audio and ``graphemes, phonemes, audio = result``
    both work.
    """

    __slots__ = ("graphemes", "phonemes", "audio", "tokens")

    def __init__(
        self,
        graphemes: str,
        phonemes: str,
        audio: np.ndarray,
        tokens: Sequence[KokoroToken] = (),
    ) -> None:
        self.graphemes = graphemes
        self.phonemes = phonemes
        self.audio = audio
        self.tokens = list(tokens)

    def _fields(self) -> tuple[str, str, np.ndarray]:
        return (self.graphemes, self.phonemes, self.audio)

    def __getitem__(self, index: int) -> Any:
        return self._fields()[index]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._fields())

    def __len__(self) -> int:
        return 3

    def __repr__(self) -> str:
        return (
            f"KokoroChunk(graphemes={self.graphemes!r}, "
            f"phonemes={self.phonemes!r}, samples={self.audio.size})"
        )


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteConfig:
    """Non-secret description of where the remote backend lives."""

    app_name: str = DEFAULT_APP_NAME
    function_name: str = DEFAULT_FUNCTION_NAME
    export_function_name: str = DEFAULT_EXPORT_FUNCTION_NAME
    transport: str = DEFAULT_TRANSPORT
    health_url: str | None = None
    gpu: str = "T4"
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS
    lang_code: str = DEFAULT_LANG_CODE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RemoteConfig":
        env = os.environ if env is None else env
        transport = (env.get("MODAL_KOKORO_TRANSPORT") or DEFAULT_TRANSPORT).strip().lower()
        if transport not in VALID_TRANSPORTS:
            logger.warning(
                "Unknown MODAL_KOKORO_TRANSPORT=%r, falling back to %r",
                transport,
                DEFAULT_TRANSPORT,
            )
            transport = DEFAULT_TRANSPORT
        timeout_raw = env.get("MODAL_KOKORO_TIMEOUT_SECONDS")
        try:
            timeout_s = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SECONDS
        except ValueError:
            logger.warning("Invalid MODAL_KOKORO_TIMEOUT_SECONDS=%r, using default", timeout_raw)
            timeout_s = DEFAULT_TIMEOUT_SECONDS
        health_url = (env.get("MODAL_KOKORO_HEALTH_URL") or "").strip() or None
        return cls(
            app_name=(env.get("MODAL_KOKORO_APP_NAME") or DEFAULT_APP_NAME).strip(),
            function_name=(env.get("MODAL_KOKORO_FUNCTION_NAME") or DEFAULT_FUNCTION_NAME).strip(),
            export_function_name=(
                env.get("MODAL_KOKORO_EXPORT_FUNCTION_NAME") or DEFAULT_EXPORT_FUNCTION_NAME
            ).strip(),
            transport=transport,
            health_url=health_url,
            gpu=(env.get("MODAL_KOKORO_GPU") or "T4").strip(),
            timeout_s=timeout_s,
            lang_code=(env.get("MODAL_KOKORO_LANG_CODE") or DEFAULT_LANG_CODE).strip(),
        )

    def describe(self) -> dict[str, Any]:
        """Secret-free view of this configuration, safe to return over HTTP.

        ``gpu_preference`` is what *this machine* would ask for at deploy time;
        it is not evidence of what is deployed. The probe reports what the
        deployed app says about itself separately, under ``deployed``.
        """
        return {
            "app_name": self.app_name,
            "function_name": self.function_name,
            "export_function_name": self.export_function_name,
            "transport": self.transport,
            "gpu_preference": self.gpu,
            "timeout_s": self.timeout_s,
            "health_url": self.health_url,
        }


def modal_credentials_present(env: Mapping[str, str] | None = None) -> bool:
    """True when Modal credentials are available from the environment or ``~/.modal.toml``.

    Only presence is reported; the values are never read into a variable that
    could end up in a log line.
    """
    env = os.environ if env is None else env
    if env.get("MODAL_TOKEN_ID") and env.get("MODAL_TOKEN_SECRET"):
        return True
    config_path = Path(env.get("MODAL_CONFIG_PATH") or Path.home() / ".modal.toml")
    try:
        with config_path.open("rb") as handle:
            profiles = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    for profile in profiles.values():
        if isinstance(profile, dict) and profile.get("token_id") and profile.get("token_secret"):
            return True
    return False


def _modal_sdk_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("modal") is not None


def resolve_transport(
    config: RemoteConfig,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Decide which transport to actually use, or ``None`` when none can work.

    ``MODAL_KOKORO_TRANSPORT`` wins when it names a transport explicitly;
    ``auto`` (the default) prefers the authenticated SDK and only falls back to
    HTTP when a health/endpoint URL is configured.
    """
    env = os.environ if env is None else env
    if config.transport == "sdk":
        return "sdk"
    if config.transport == "http":
        return "http" if config.health_url or env.get("MODAL_KOKORO_URL") else None
    if modal_credentials_present(env) and _modal_sdk_installed():
        return "sdk"
    if config.health_url or env.get("MODAL_KOKORO_URL"):
        return "http"
    return None


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------


def _describe_exception(exc: BaseException) -> str:
    """Human-readable reason for a Modal-side failure, without the traceback dump."""
    name = type(exc).__name__
    if name in ("AuthError", "TokenError"):
        return f"Modal authentication failed ({name}): check MODAL_TOKEN_ID/MODAL_TOKEN_SECRET"
    if name in ("NotFoundError", "InvalidError"):
        return f"Modal app/function not found ({name}): has `modal deploy` been run?"
    if name in ("TimeoutError", "FunctionTimeoutError", "InputTimeoutError"):
        return f"Modal call timed out server-side ({name})"
    if name in ("ConnectionError", "GRPCError", "InternalError"):
        return f"Could not reach the Modal API ({name})"
    return f"{name}: {exc}"


class _SdkTransport:
    """Runs ``synthesize`` on Modal through the Python SDK.

    The SDK is chosen over an HTTP endpoint because it authenticates with the
    user's existing Modal token, has no 150 s HTTP request limit, and exposes no
    public GPU URL — a public endpoint on a GPU function is a standing cost risk
    (docs/research/kokoro-runtime-picks.md §4).
    """

    def __init__(self, config: RemoteConfig, env: Mapping[str, str] | None = None) -> None:
        self._config = config
        self._env = os.environ if env is None else env
        self._lock = threading.Lock()
        self._function: Any = None

    def _resolve_function(self) -> Any:
        if not modal_credentials_present(self._env):
            raise RemoteSynthesisError(
                "No Modal credentials found: set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET "
                "or run `modal token new` to write ~/.modal.toml"
            )
        try:
            import modal  # imported lazily: the backend must start without it
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise RemoteSynthesisError(
                "The `modal` package is not installed; `pip install modal` "
                f"or set KOKORO_BACKEND=local ({exc})"
            ) from exc

        with self._lock:
            function = self._function
        if function is None:
            try:
                # `from_name` is lazy and never fails on its own; the first
                # remote()/spawn() call is what resolves it against Modal.
                function = modal.Function.from_name(
                    self._config.app_name, self._config.function_name
                )
            except Exception as exc:
                raise RemoteSynthesisError(
                    f"Could not look up Modal function "
                    f"{self._config.app_name}/{self._config.function_name}: "
                    f"{_describe_exception(exc)}"
                ) from exc
            with self._lock:
                self._function = function
        return function

    def _forget_function(self) -> None:
        with self._lock:
            self._function = None

    def invoke(self, payload: dict) -> dict:
        function = self._resolve_function()
        try:
            return function.remote(payload)
        except Exception as exc:
            self._forget_function()  # the handle may be stale; re-resolve next time
            raise RemoteSynthesisError(
                f"Modal synthesis call failed for {self._config.app_name}/"
                f"{self._config.function_name}: {_describe_exception(exc)}"
            ) from exc

    def spawn(self, payload: dict) -> str:
        """Start a call without waiting for it, so a container can warm up."""
        function = self._resolve_function()
        try:
            call = function.spawn(payload)
        except Exception as exc:
            self._forget_function()
            raise RemoteSynthesisError(
                f"Could not start a Modal warm-up call for {self._config.app_name}/"
                f"{self._config.function_name}: {_describe_exception(exc)}"
            ) from exc
        return str(getattr(call, "object_id", None) or "spawned")

    def runner_count(self) -> int | None:
        """How many containers are alive for the deployed function right now.

        ``None`` means "the API could not say", which callers must treat as
        unknown rather than as zero.
        """
        try:
            stats = self._resolve_function().get_current_stats()
        except Exception:
            logger.debug("could not read Modal function stats", exc_info=True)
            return None
        runners = getattr(stats, "num_total_runners", None)
        if runners is None:
            return None
        try:
            return int(runners)
        except (TypeError, ValueError):
            return None

    def map_batches(self, payloads: Sequence[dict]) -> Iterator[dict]:
        """Run one payload per container with ``Function.map``.

        ``order_outputs=False`` is the point: results are consumed as each
        container finishes, which is what lets the export report progress
        instead of waiting for the slowest batch.
        """
        function = self._resolve_function()
        try:
            return iter(function.map(list(payloads), order_outputs=False))
        except Exception as exc:
            self._forget_function()
            raise RemoteSynthesisError(
                f"Could not start Modal batch export for {self._config.app_name}/"
                f"{self._config.export_function_name}: {_describe_exception(exc)}"
            ) from exc


def _http_invoke_factory(config: RemoteConfig, env: Mapping[str, str] | None = None) -> Callable[[dict], dict]:
    """Return a callable that POSTs to a deployed Modal web endpoint."""
    env = os.environ if env is None else env
    url = env.get("MODAL_KOKORO_URL")

    def invoke(payload: dict) -> dict:
        if not url:
            raise RemoteSynthesisError(
                "MODAL_KOKORO_TRANSPORT=http requires MODAL_KOKORO_URL to point at a "
                "deployed Modal web endpoint"
            )
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - httpx is a project dependency
            raise RemoteSynthesisError(f"httpx is required for the HTTP transport ({exc})") from exc
        try:
            response = httpx.post(url, json=payload, timeout=config.timeout_s)
        except httpx.HTTPError as exc:
            raise RemoteSynthesisError(f"Network error calling {url}: {exc}") from exc
        if response.status_code != 200:
            body = response.text[:300].replace("\n", " ")
            raise RemoteSynthesisError(
                f"Modal endpoint returned HTTP {response.status_code}: {body}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise RemoteSynthesisError(f"Modal endpoint returned non-JSON body: {exc}") from exc

    return invoke


# --------------------------------------------------------------------------
# Response decoding
# --------------------------------------------------------------------------


def _decode_tokens(raw_tokens: Any) -> list[KokoroToken]:
    tokens: list[KokoroToken] = []
    for raw in raw_tokens or []:
        if not isinstance(raw, dict):
            continue
        tokens.append(
            KokoroToken(
                text=str(raw.get("text") or ""),
                phonemes=str(raw.get("phonemes") or ""),
                start_ts=float(raw.get("start_ts") or 0.0),
                end_ts=float(raw.get("end_ts") or 0.0),
            )
        )
    return tokens


def _decode_batch(result: Any, config: RemoteConfig) -> "BatchAudio":
    """Decode one ``synthesize_batch`` result into ``(batch_index, float32 audio)``.

    Batches travel as FLAC rather than raw float32: a ten-minute batch is
    ~10-15 MB instead of ~60 MB, and libsndfile decodes it back to exactly the
    sample count that went in (verified against the original), so the word
    timestamps derived from sentence offsets stay aligned.
    """
    if not isinstance(result, dict):
        raise RemoteSynthesisError(
            f"Modal batch returned {type(result).__name__}, expected a dict payload"
        )
    batch_index = result.get("batch_index")
    if batch_index is None:
        raise RemoteSynthesisError("Modal batch result is missing batch_index")
    if result.get("sample_rate") != SAMPLE_RATE:
        raise RemoteSynthesisError(
            f"Modal batch returned sample_rate={result.get('sample_rate')!r}, "
            f"expected {SAMPLE_RATE}"
        )
    encoded = result.get("flac_b64")
    if not encoded:
        raise RemoteSynthesisError(f"Modal batch {batch_index} is missing flac_b64")

    try:
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - soundfile is a project dependency
        raise RemoteSynthesisError(f"soundfile is required to decode export batches ({exc})") from exc
    try:
        audio, rate = sf.read(
            io.BytesIO(base64.b64decode(encoded)), dtype="float32", always_2d=False
        )
    except Exception as exc:
        raise RemoteSynthesisError(f"Modal batch {batch_index} is not decodable FLAC: {exc}") from exc
    if rate != SAMPLE_RATE:
        raise RemoteSynthesisError(
            f"Modal batch {batch_index} decoded at {rate} Hz, expected {SAMPLE_RATE}"
        )
    audio = np.ascontiguousarray(np.asarray(audio, dtype=np.float32).reshape(-1))
    if audio.size == 0:
        raise RemoteSynthesisError(f"Modal batch {batch_index} decoded to zero samples")

    lengths = result.get("sentence_samples")
    if lengths is not None and not isinstance(lengths, list):
        raise RemoteSynthesisError(
            f"Modal batch {batch_index} returned sentence_samples="
            f"{type(lengths).__name__}, expected a list"
        )
    return BatchAudio(
        batch_index=int(batch_index),
        audio=audio,
        sentence_samples=[int(n) for n in lengths] if lengths else [],
    )


def decode_results(response: Any, config: RemoteConfig) -> list[list[KokoroChunk]]:
    """Turn a wire response into one chunk group per input text.

    Raises ``RemoteSynthesisError`` explaining exactly what was wrong rather
    than returning something that looks like a successful synthesis of silence.
    """
    if not isinstance(response, dict):
        raise RemoteSynthesisError(
            f"Modal returned {type(response).__name__}, expected a dict payload"
        )

    sample_rate = response.get("sample_rate")
    if sample_rate != SAMPLE_RATE:
        raise RemoteSynthesisError(
            f"Modal returned sample_rate={sample_rate!r}, expected {SAMPLE_RATE}"
        )

    raw_results = response.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise RemoteSynthesisError(
            f"Modal returned no results for app {config.app_name}/{config.function_name}"
        )

    groups: list[list[KokoroChunk]] = []
    for result_index, raw_result in enumerate(raw_results):
        if not isinstance(raw_result, dict):
            raise RemoteSynthesisError(f"Modal result {result_index} is not an object")
        raw_chunks = raw_result.get("chunks")
        if not isinstance(raw_chunks, list) or not raw_chunks:
            raise RemoteSynthesisError(
                f"Modal returned no audio chunks for input {result_index}"
            )

        chunks: list[KokoroChunk] = []
        for index, raw in enumerate(raw_chunks):
            if not isinstance(raw, dict) or not raw.get("audio_b64"):
                raise RemoteSynthesisError(
                    f"Modal chunk {result_index}.{index} is missing audio_b64"
                )
            try:
                audio = np.frombuffer(base64.b64decode(raw["audio_b64"]), dtype="<f4")
            except (ValueError, TypeError) as exc:
                raise RemoteSynthesisError(
                    f"Modal chunk {result_index}.{index} is not base64 float32: {exc}"
                ) from exc
            if audio.size == 0:
                raise RemoteSynthesisError(
                    f"Modal chunk {result_index}.{index} decoded to zero samples"
                )
            chunks.append(
                KokoroChunk(
                    graphemes=str(raw.get("graphemes") or ""),
                    phonemes=str(raw.get("phonemes") or ""),
                    audio=np.ascontiguousarray(audio, dtype=np.float32),
                    tokens=_decode_tokens(raw.get("tokens")),
                )
            )
        groups.append(chunks)
    return groups


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------


@dataclass
class ModalKokoroClient:
    """Call-compatible stand-in for the local ``KPipeline`` object."""

    config: RemoteConfig = field(default_factory=RemoteConfig.from_env)
    invoke: Callable[[dict], dict] | None = None
    warm: Callable[[dict], str] | None = None
    # Injectable so an export batch run can be driven without Modal (tests, and
    # any future caller that wants a different fan-out strategy).
    batch_mapper: Callable[[Sequence[dict]], Iterator[dict]] | None = None
    _executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="modal-tts"),
        repr=False,
    )
    _resolved_transport: str | None = None
    _sdk: "_SdkTransport | None" = field(default=None, repr=False)
    _last_success_at: float | None = field(default=None, repr=False)
    _warm_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if self.invoke is None:
            self._resolved_transport = resolve_transport(self.config)
            if self._resolved_transport == "sdk":
                sdk = _SdkTransport(self.config)
                self._sdk = sdk
                self.invoke = sdk.invoke
                self.warm = sdk.spawn
            elif self._resolved_transport == "http":
                self.invoke = _http_invoke_factory(self.config)
            else:
                self.invoke = self._unconfigured_invoke
        elif self._resolved_transport is None:
            self._resolved_transport = "injected"

    @staticmethod
    def _unconfigured_invoke(payload: dict) -> dict:
        raise RemoteSynthesisError(
            "Remote Kokoro backend is not configured: set MODAL_TOKEN_ID/MODAL_TOKEN_SECRET "
            "(Modal SDK transport) or MODAL_KOKORO_URL (HTTP transport)"
        )

    @property
    def transport(self) -> str | None:
        return self._resolved_transport

    def build_payload(self, texts: Sequence[str], voice: str, speed: float) -> dict[str, Any]:
        return {
            "texts": list(texts),
            "voice": voice,
            "speed": float(speed),
            "lang_code": self.config.lang_code,
        }

    def __call__(
        self,
        text: str,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
    ) -> list[KokoroChunk]:
        """Synthesize ``text`` on the Modal GPU and return its decoded chunks."""
        if not text or not text.strip():
            return []
        return self.synthesize_many([text], voice=voice, speed=speed)[0]

    def synthesize_many(
        self,
        texts: Sequence[str],
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
    ) -> list[list[KokoroChunk]]:
        """Synthesize several sentences in one call, in order.

        Used by the (not yet wired) chapter prefetch: ``KModel`` handles batch
        size 1 only, so "batching" means one request covering several inputs and
        keeping the GPU container up for one round-trip instead of N.
        """
        if not texts:
            return []
        payload = self.build_payload(texts, voice, speed)
        response = self._invoke_with_timeout(payload)
        groups = decode_results(response, self.config)
        if len(groups) != len(texts):
            raise RemoteSynthesisError(
                f"Modal returned {len(groups)} result group(s) for {len(texts)} input(s)"
            )
        return groups

    def warmup(self, text: str = WARMUP_TEXT) -> bool:
        """Ask Modal to start a GPU container without waiting for the answer.

        Intended for "the reader just opened a book": a cheap fire-and-forget
        call so the container, image and weights are warm by the time the first
        sentence is requested. Costs one container start (~seconds of GPU time),
        so call it on intent, not on every navigation.
        """
        if self.warm is None:
            raise RemoteSynthesisError(
                "Warm-up requires the Modal SDK transport "
                f"(current transport: {self._resolved_transport})"
            )
        self.warm(self.build_payload([text], DEFAULT_VOICE, 1.0))
        return True

    def runner_count(self) -> int | None:
        """Live container count, or ``None`` when the API cannot say."""
        if self._sdk is None:
            return None
        return self._sdk.runner_count()

    def synthesize_batches(
        self,
        batches: Sequence[dict],
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
        mapper: Callable[[Sequence[dict]], Iterator[dict]] | None = None,
    ) -> Iterator[BatchAudio]:
        """Synthesize export batches in parallel, yielding each finished batch.

        One batch is one Modal container, so a 12-chapter book runs on up to
        ``max_containers`` GPUs at once instead of one round trip per sentence.
        Results arrive in *completion* order (``order_outputs=False``); the
        caller reassembles by ``batch_index``, which is what makes progress
        reporting possible. ``mapper`` is injectable so tests never touch the
        network.
        """
        if not batches:
            return
        payloads = [
            {
                "batch_index": int(batch["batch_index"]),
                "sentences": list(batch["sentences"]),
                "voice": voice,
                "speed": float(speed),
                "lang_code": self.config.lang_code,
            }
            for batch in batches
        ]
        run = mapper or self.batch_mapper or self._default_batch_mapper()
        try:
            for result in run(payloads):
                yield _decode_batch(result, self.config)
        except RemoteSynthesisError:
            raise
        except Exception as exc:
            raise RemoteSynthesisError(
                f"Modal batch export failed for {self.config.app_name}/"
                f"{self.config.export_function_name}: {_describe_exception(exc)}"
            ) from exc

    def _default_batch_mapper(self) -> Callable[[Sequence[dict]], Iterator[dict]]:
        if self._sdk is None:
            raise RemoteSynthesisError(
                "Batch export requires the Modal SDK transport "
                f"(current transport: {self._resolved_transport})"
            )
        return self._sdk.map_batches

    def _invoke_with_timeout(self, payload: dict) -> dict:
        assert self.invoke is not None  # set in __post_init__
        started = time.perf_counter()
        future = self._executor.submit(self.invoke, payload)
        try:
            # A cold start legitimately takes tens of seconds, so the cap is
            # generous — but it must exist, or a wedged call would stall the
            # reader forever.
            result = future.result(timeout=self.config.timeout_s)
        except FutureTimeoutError as exc:
            raise RemoteSynthesisError(
                f"Modal synthesis did not answer within {self.config.timeout_s:.0f}s "
                f"(application {self.config.app_name}/{self.config.function_name}); "
                "the GPU container may be cold-starting or stuck"
            ) from exc
        except RemoteSynthesisError:
            raise
        except Exception as exc:
            # A transport that raises something of its own still surfaces as the
            # one exception type callers need to know about.
            raise RemoteSynthesisError(
                f"Modal synthesis failed for {self.config.app_name}/"
                f"{self.config.function_name}: {_describe_exception(exc)}"
            ) from exc
        self._last_success_at = time.monotonic()
        logger.debug("modal synthesis round-trip: %.0f ms", (time.perf_counter() - started) * 1000)
        return result

    def is_warm(self) -> bool:
        """True when a call has succeeded recently enough to assume a live container."""
        with self._warm_lock:
            last = self._last_success_at
        return last is not None and (time.monotonic() - last) < WARM_WINDOW_SECONDS


# --------------------------------------------------------------------------
# Reachability probe (used by GET /api/system/capabilities)
# --------------------------------------------------------------------------

_probe_lock = threading.Lock()
_probe_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _check_health_url(url: str, timeout_s: float) -> dict[str, Any]:
    """GET a deployed health endpoint; return its JSON self-description."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx is a project dependency
        raise RemoteSynthesisError(f"httpx is required to probe {url} ({exc})") from exc
    try:
        response = httpx.get(url, timeout=timeout_s)
    except httpx.HTTPError as exc:
        raise RemoteSynthesisError(f"Network error probing {url}: {exc}") from exc
    if response.status_code != 200:
        raise RemoteSynthesisError(f"{url} returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _check_sdk(app_name: str, function_name: str) -> None:
    """Force a real Modal API round-trip.

    ``Function.from_name`` is documented as *lazy*: it builds a handle without
    contacting Modal at all. Measured on modal 1.5.5 it returns successfully in
    0s for an app that does not exist, and equally successfully on a machine
    with no network — so on its own it proves nothing. ``hydrate()`` is what
    actually resolves the handle against the Modal servers, and it raises
    ``NotFoundError`` for a missing app/function and ``ConnectionError`` when the
    API is unreachable.
    """
    if not modal_credentials_present():
        raise RemoteSynthesisError("No Modal credentials found")
    try:
        import modal
    except ImportError as exc:
        raise RemoteSynthesisError(f"The `modal` package is not installed ({exc})") from exc
    try:
        modal.Function.from_name(app_name, function_name).hydrate()
    except Exception as exc:
        raise RemoteSynthesisError(_describe_exception(exc)) from exc


def probe(
    config: RemoteConfig | None = None,
    env: Mapping[str, str] | None = None,
    checker: Callable[[], Any] | None = None,
    force: bool = False,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Report — by actually asking — whether a remote backend is usable.

    ``checker`` is injectable so tests never touch the network.  The answer is
    cached for ``PROBE_TTL_SECONDS`` because the SDK check is a real Modal API
    round-trip and this runs on an HTTP request path.  ``timeout_s`` overrides
    the cap (startup uses a shorter one than the endpoint does).
    """
    env = os.environ if env is None else env
    config = config or RemoteConfig.from_env(env)
    transport = resolve_transport(config, env)
    credentials = modal_credentials_present(env)
    result: dict[str, Any] = {
        **config.describe(),
        "configured": transport is not None,
        "resolved_transport": transport,
        "credentials_present": credentials,
        "reachable": False,
        # What the *deployed* app says about itself, when the check can see it.
        "deployed": None,
        "error": None,
    }

    if transport is None:
        result["error"] = (
            "No Modal credentials and no MODAL_KOKORO_HEALTH_URL; "
            "run `modal token new` or set the environment variables"
        )
        return result

    cache_key = f"{transport}:{config.app_name}:{config.function_name}:{config.health_url}"
    with _probe_lock:
        cached = _probe_cache.get(cache_key)
    if cached is not None and not force and (time.monotonic() - cached[0]) < PROBE_TTL_SECONDS:
        return dict(cached[1])

    probe_timeout = timeout_s if timeout_s is not None else min(config.timeout_s, 10.0)
    if checker is None:
        if config.health_url:
            # Cheapest real check: a genuine HTTP round-trip to the deployed app.
            health_url = config.health_url
            checker = lambda: _check_health_url(health_url, probe_timeout)
        elif transport == "sdk":
            # No health URL, so resolve the handle for real against the Modal API.
            checker = lambda: _check_sdk(config.app_name, config.function_name)
        else:
            url = env.get("MODAL_KOKORO_URL")
            checker = lambda: _check_health_url(str(url), probe_timeout)

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(checker)
        observed = future.result(timeout=probe_timeout)
        result["reachable"] = True
        if isinstance(observed, dict) and observed:
            result["deployed"] = observed
    except FutureTimeoutError:
        result["error"] = f"reachability check did not answer within {probe_timeout:.0f}s"
    except RemoteSynthesisError as exc:
        result["error"] = str(exc)
    except Exception as exc:  # a checker must never take the endpoint down
        result["error"] = _describe_exception(exc)
    finally:
        executor.shutdown(wait=False)

    result["checked_at"] = time.time()
    with _probe_lock:
        _probe_cache[cache_key] = (time.monotonic(), result)
    return dict(result)


def reset_probe_cache() -> None:
    """Drop cached probe results (tests, and after a deploy)."""
    with _probe_lock:
        _probe_cache.clear()
