from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from backend.config import AppConfig, RenderMode, VideoCodec
from backend.logger import append_jsonl
from backend.renderer import render_one


@dataclass
class BenchmarkRow:
    mode: RenderMode
    codec: str
    success: bool
    elapsed_seconds: Optional[float]
    projected_6_videos_seconds: Optional[float]
    output_size_bytes: Optional[int]
    error: Optional[str]


@dataclass
class BenchmarkReport:
    rows: List[BenchmarkRow]
    recommendation: str
    target_60s_met: bool


def _codec_from_command(command: str) -> str:
    if "h264_nvenc" in command:
        return "h264_nvenc"
    if "h264_amf" in command:
        return "h264_amf"
    if "libx264" in command:
        return "libx264"
    return "unknown"


def run_benchmark(input_path: str | Path, config: AppConfig, codec: Optional[VideoCodec] = None) -> BenchmarkReport:
    input_path = Path(input_path)
    rows: List[BenchmarkRow] = []
    original_move = config.move_original_on_success
    config.move_original_on_success = False

    try:
        for mode in ["ultra", "pdv", "balanced", "quality"]:
            with tempfile.NamedTemporaryFile(prefix=f"bench_{mode}_", suffix=input_path.suffix, delete=False) as tmp:
                tmp_path = Path(tmp.name)
            shutil.copy2(input_path, tmp_path)
            try:
                result = render_one(tmp_path, config, mode=mode, move_original=False, codec=codec)
                elapsed = result.elapsed_seconds
                rows.append(BenchmarkRow(
                    mode=mode,  # type: ignore[arg-type]
                    codec=_codec_from_command(result.command),
                    success=True,
                    elapsed_seconds=elapsed,
                    projected_6_videos_seconds=round(elapsed * 6, 3),
                    output_size_bytes=result.output_size_bytes,
                    error=None,
                ))
            except Exception as exc:
                rows.append(BenchmarkRow(
                    mode=mode,  # type: ignore[arg-type]
                    codec=codec or config.export.codec,
                    success=False,
                    elapsed_seconds=None,
                    projected_6_videos_seconds=None,
                    output_size_bytes=None,
                    error=str(exc),
                ))
            finally:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
    finally:
        config.move_original_on_success = original_move

    successful_under_target = [r for r in rows if r.success and r.projected_6_videos_seconds is not None and r.projected_6_videos_seconds <= 60]
    if successful_under_target:
        # Preferir melhor qualidade entre as que batem a meta.
        priority = {"quality": 4, "balanced": 3, "pdv": 2, "ultra": 1}
        chosen = sorted(successful_under_target, key=lambda r: priority[r.mode], reverse=True)[0]
        recommendation = f"Usar modo {chosen.mode} com {chosen.codec}: projeção de 6 vídeos em {chosen.projected_6_videos_seconds:.2f}s."
        target = True
    else:
        successful = [r for r in rows if r.success and r.elapsed_seconds is not None]
        if successful:
            fastest = sorted(successful, key=lambda r: r.elapsed_seconds or 999999)[0]
            recommendation = (
                f"Nenhum modo bateu a meta de 60s para 6 vídeos. "
                f"Modo mais rápido: {fastest.mode} com {fastest.codec}, projeção {fastest.projected_6_videos_seconds:.2f}s. "
                "Gargalo provável: CPU/libx264, chromakey, preset pesado, rotação/escala ou decodificação. "
                "Para buscar a meta real, teste no PC com GPU NVIDIA/NVENC ou AMD/AMF, ou exporte o preset com alpha."
            )
        else:
            recommendation = "Todos os testes falharam. Verifique FFmpeg, preset e vídeo de entrada. Use --codec auto ou --codec libx264 para fallback em CPU."
        target = False

    report = BenchmarkReport(rows=rows, recommendation=recommendation, target_60s_met=target)
    append_jsonl(config.logs_path, "benchmark.jsonl", {"rows": [asdict(r) for r in rows], "recommendation": recommendation})
    return report


def report_to_json(report: BenchmarkReport) -> str:
    return json.dumps({"rows": [asdict(r) for r in report.rows], "recommendation": report.recommendation, "target_60s_met": report.target_60s_met}, ensure_ascii=False, indent=2)
