from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from backend.app_info import APP_NAME, APP_VERSION
from backend.benchmark import report_to_json, run_benchmark
from backend.config import AppConfig, MAX_PARALLEL_WORKERS, clamp_parallel_workers, load_config, save_config
from backend.ffmpeg_builder import (
    ffmpeg_supports_amf,
    ffmpeg_supports_libx264,
    ffmpeg_supports_nvenc,
    require_ffmpeg,
    test_amf_runtime,
    test_nvenc_runtime,
)
from backend.ffprobe_reader import probe_media, require_ffprobe
from backend.renderer import RenderError, render_one, render_pair, validate_environment
from backend.history import RenderHistory
from backend.input_selector import next_candidate_video, next_candidate_video_group, next_manual_82_video
from backend.watcher import render_configured_candidates, run_polling_watcher, run_watchdog_watcher


def _load(config_path: str) -> AppConfig:
    cfg = load_config(config_path)
    # Se o config foi criado por padrão, manter root no diretório do config.
    if cfg.project_root == ".":
        cfg.project_root = str(Path(config_path).resolve().parent.parent)
    return cfg


def cmd_doctor(args) -> int:
    cfg = _load(args.config)
    checks = []
    checks.append({"app": APP_NAME, "version": APP_VERSION, "ok": True})
    nvenc_required = cfg.export.codec == "h264_nvenc"
    amf_required = cfg.export.codec == "h264_amf"
    x264_required = cfg.export.codec == "libx264"
    nvenc_runtime_ok = False
    amf_runtime_ok = False
    x264_ok = False
    try:
        checks.append({"ffmpeg": require_ffmpeg(), "ok": True})
    except Exception as exc:
        checks.append({"ffmpeg": None, "ok": False, "error": str(exc)})
    try:
        checks.append({"ffprobe": require_ffprobe(), "ok": True})
    except Exception as exc:
        checks.append({"ffprobe": None, "ok": False, "error": str(exc)})
    try:
        listed, _ = ffmpeg_supports_nvenc()
        nvenc_runtime_ok, runtime_detail = test_nvenc_runtime()
        checks.append({
            "h264_nvenc_listed": listed,
            "h264_nvenc_runtime": nvenc_runtime_ok,
            "required": nvenc_required,
            "ok": nvenc_runtime_ok or not nvenc_required,
            "detail": runtime_detail[:500],
        })
    except Exception as exc:
        checks.append({"h264_nvenc": False, "required": nvenc_required, "ok": not nvenc_required, "error": str(exc)})
    try:
        listed, _ = ffmpeg_supports_amf()
        amf_runtime_ok, runtime_detail = test_amf_runtime()
        checks.append({
            "h264_amf_listed": listed,
            "h264_amf_runtime": amf_runtime_ok,
            "required": amf_required,
            "ok": amf_runtime_ok or not amf_required,
            "detail": runtime_detail[:500],
        })
    except Exception as exc:
        checks.append({"h264_amf": False, "required": amf_required, "ok": not amf_required, "error": str(exc)})
    try:
        x264_ok, _ = ffmpeg_supports_libx264()
        checks.append({"libx264": x264_ok, "required": x264_required, "ok": x264_ok or not x264_required})
    except Exception as exc:
        checks.append({"libx264": False, "required": x264_required, "ok": not x264_required, "error": str(exc)})
    if cfg.export.codec == "auto":
        encoder_ok = nvenc_runtime_ok or amf_runtime_ok or x264_ok
    elif cfg.export.codec == "h264_nvenc":
        encoder_ok = nvenc_runtime_ok
    elif cfg.export.codec == "h264_amf":
        encoder_ok = amf_runtime_ok
    else:
        encoder_ok = x264_ok
    checks.append({"usable_encoder": encoder_ok, "ok": encoder_ok})
    checks.append({"configured_codec": cfg.export.codec, "ok": True})
    checks.append({"configured_mode": cfg.mode, "parallel_workers": cfg.parallel_workers, "ok": True})
    checks.append({"preset": str(cfg.preset_path), "exists": cfg.preset_path.exists(), "ok": cfg.preset_path.exists()})
    checks.append({"pair_preset": str(cfg.pair_preset_path), "exists": cfg.pair_preset_path.exists(), "ok": cfg.pair_preset_path.exists()})
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if all(item.get("ok") for item in checks) else 2


def cmd_probe(args) -> int:
    info = probe_media(args.file)
    print(json.dumps(info.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_render(args) -> int:
    cfg = _load(args.config)
    if args.preset:
        cfg.preset_file = args.preset
    if args.output_dir:
        cfg.output_dir = args.output_dir
    if args.mode:
        cfg.mode = args.mode
    if args.preset_type:
        cfg.export.preset_type = args.preset_type
    if args.codec:
        cfg.export.codec = args.codec
    if args.no_move:
        cfg.move_original_on_success = False
    if args.no_history:
        cfg.history_enabled = False
    result = render_one(args.input, cfg, mode=args.mode, codec=args.codec)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0



def cmd_render_next(args) -> int:
    cfg = _load(args.config)
    if args.preset:
        cfg.preset_file = args.preset
    if args.output_dir:
        cfg.output_dir = args.output_dir
    if args.mode:
        cfg.mode = args.mode
    if args.preset_type:
        cfg.export.preset_type = args.preset_type
    if args.codec:
        cfg.export.codec = args.codec
    if args.no_move:
        cfg.move_original_on_success = False
    if args.no_history:
        cfg.history_enabled = False

    history = RenderHistory(cfg.history_path, cfg.input_path) if cfg.history_enabled else None
    candidate = next_candidate_video_group(cfg, history)
    if not candidate:
        print("Nenhum vídeo elegível encontrado. Regra: se existir pasta *_AUTO, vídeos soltos são ignorados.")
        return 4

    if candidate.is_pair:
        input_81, input_82 = candidate.paths
        print(f"Par selecionado: {input_81} + {input_82}")
        result = render_pair(input_81, input_82, cfg, mode=args.mode, codec=args.codec)
    else:
        input_file = candidate.primary_path
        print(f"Entrada selecionada: {input_file}")
        result = render_one(input_file, cfg, mode=args.mode, codec=args.codec)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


def cmd_render_manual_82(args) -> int:
    cfg = _load(args.config)
    if args.preset:
        cfg.preset_file = args.preset
    if args.output_dir:
        cfg.output_dir = args.output_dir
    if args.input_dir:
        cfg.input_dir = args.input_dir
    if args.mode:
        cfg.mode = args.mode
    if args.preset_type:
        cfg.export.preset_type = args.preset_type
    if args.codec:
        cfg.export.codec = args.codec
    if args.no_move:
        cfg.move_original_on_success = False
    if args.no_history:
        cfg.history_enabled = False

    history = RenderHistory(cfg.history_path, cfg.input_path) if cfg.history_enabled else None
    candidate = next_manual_82_video(cfg, history)
    if not candidate:
        print("Nenhum vídeo 82 elegível encontrado na pasta selecionada.")
        return 4

    print(f"Vídeo 82 selecionado: {candidate}")
    result = render_one(candidate, cfg, mode=args.mode, codec=args.codec)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


def cmd_render_queue(args) -> int:
    cfg = _load(args.config)
    if args.preset:
        cfg.preset_file = args.preset
    if args.output_dir:
        cfg.output_dir = args.output_dir
    if args.mode:
        cfg.mode = args.mode
    if args.preset_type:
        cfg.export.preset_type = args.preset_type
    if args.codec:
        cfg.export.codec = args.codec
    if args.workers:
        cfg.parallel_workers = clamp_parallel_workers(args.workers)
    if args.no_move:
        cfg.move_original_on_success = False
    if args.no_history:
        cfg.history_enabled = False

    validate_environment(cfg)
    processed = render_configured_candidates(cfg, print)
    if processed <= 0:
        print("Nenhum vídeo elegível encontrado. Regra: se existir pasta *_AUTO, vídeos soltos são ignorados.")
        return 4
    print(f"Fila finalizada: {processed} vídeo(s) processado(s).")
    return 0


def cmd_benchmark_next(args) -> int:
    cfg = _load(args.config)
    if args.preset:
        cfg.preset_file = args.preset
    if args.output_dir:
        cfg.output_dir = args.output_dir
    if args.codec:
        cfg.export.codec = args.codec

    history = RenderHistory(cfg.history_path, cfg.input_path) if cfg.history_enabled else None
    candidate = next_candidate_video(cfg, history)
    if not candidate:
        print("Nenhum vídeo elegível encontrado para benchmark.")
        return 4

    print(f"Entrada selecionada para benchmark: {candidate}")
    report = run_benchmark(candidate, cfg, codec=args.codec)
    print(report_to_json(report))
    return 0 if report.target_60s_met else 3


def cmd_history_clear(args) -> int:
    cfg = _load(args.config)
    history = RenderHistory(cfg.history_path, cfg.input_path)
    history.clear()
    print(f"Histórico limpo: {cfg.history_path}")
    return 0

def cmd_watch(args) -> int:
    cfg = _load(args.config)
    if args.polling:
        run_polling_watcher(cfg)
    else:
        run_watchdog_watcher(cfg)
    return 0


def cmd_benchmark(args) -> int:
    cfg = _load(args.config)
    if args.preset:
        cfg.preset_file = args.preset
    if args.output_dir:
        cfg.output_dir = args.output_dir
    if args.codec:
        cfg.export.codec = args.codec
    report = run_benchmark(args.input, cfg, codec=args.codec)
    print(report_to_json(report))
    return 0 if report.target_60s_met else 3


def cmd_init(args) -> int:
    cfg = _load(args.config)
    for path in [cfg.preset_path.parent, cfg.input_path, cfg.output_path, cfg.processed_path, cfg.error_path, cfg.logs_path]:
        path.mkdir(parents=True, exist_ok=True)
    save_config(cfg, args.config)
    print(f"Estrutura criada em: {cfg.root_path()}")
    return 0


def cmd_ui(args) -> int:
    from frontend.ui import launch_ui
    launch_ui(args.config)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autorender",
        description=f"{APP_NAME} {APP_VERSION}: renderizacao rapida com FFmpeg/NVENC/AMF.",
    )
    parser.add_argument("--config", default="config/settings.json", help="Caminho do settings.json")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Cria estrutura de pastas e settings padrão")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="Testa FFmpeg, FFprobe, NVENC, AMF e preset")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("probe", help="Lê metadados de um vídeo com FFprobe")
    p.add_argument("file")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("render", help="Renderiza um vídeo explícito")
    p.add_argument("--input", required=True, help="Vídeo de entrada")
    p.add_argument("--preset", help="Arquivo preset .MOV")
    p.add_argument("--output-dir", help="Pasta de saída")
    p.add_argument("--mode", choices=["ultra", "pdv", "balanced", "quality"], help="Modo de exportação")
    p.add_argument("--preset-type", choices=["auto", "alpha", "green"], help="Força alpha, chromakey ou auto")
    p.add_argument("--codec", choices=["auto", "h264_nvenc", "h264_amf", "libx264"], help="auto usa GPU NVIDIA/AMD se existir; libx264 usa CPU")
    p.add_argument("--no-move", action="store_true", help="Não mover original para processados")
    p.add_argument("--no-history", action="store_true", help="Não gravar histórico deste render")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("render-next", help="Renderiza o próximo vídeo elegível respeitando *_AUTO e histórico")
    p.add_argument("--preset", help="Arquivo preset .MOV")
    p.add_argument("--output-dir", help="Pasta de saída")
    p.add_argument("--mode", choices=["ultra", "pdv", "balanced", "quality"], help="Modo de exportação")
    p.add_argument("--preset-type", choices=["auto", "alpha", "green"], help="Força alpha, chromakey ou auto")
    p.add_argument("--codec", choices=["auto", "h264_nvenc", "h264_amf", "libx264"], help="auto usa GPU NVIDIA/AMD se existir; libx264 usa CPU")
    p.add_argument("--no-move", action="store_true", help="Não mover original para processados")
    p.add_argument("--no-history", action="store_true", help="Não gravar histórico deste render")
    p.set_defaults(func=cmd_render_next)

    p = sub.add_parser("render-manual-82", help="Renderiza somente o próximo vídeo 82 usando o preset manual")
    p.add_argument("--input-dir", help="Pasta de entrada ou pasta do ciclo selecionada")
    p.add_argument("--preset", help="Arquivo preset manual .MOV")
    p.add_argument("--output-dir", help="Pasta de saída")
    p.add_argument("--mode", choices=["ultra", "pdv", "balanced", "quality"], help="Modo de exportação")
    p.add_argument("--preset-type", choices=["auto", "alpha", "green"], help="Força alpha, chromakey ou auto")
    p.add_argument("--codec", choices=["auto", "h264_nvenc", "h264_amf", "libx264"], help="auto usa GPU NVIDIA/AMD se existir; libx264 usa CPU")
    p.add_argument("--no-move", action="store_true", help="Não mover original para processados")
    p.add_argument("--no-history", action="store_true", help="Não gravar histórico deste render")
    p.set_defaults(func=cmd_render_manual_82)

    p = sub.add_parser("render-queue", help="Renderiza a fila elegível usando parallel_workers")
    p.add_argument("--preset", help="Arquivo preset .MOV")
    p.add_argument("--output-dir", help="Pasta de saída")
    p.add_argument("--mode", choices=["ultra", "pdv", "balanced", "quality"], help="Modo de exportação")
    p.add_argument("--preset-type", choices=["auto", "alpha", "green"], help="Força alpha, chromakey ou auto")
    p.add_argument("--codec", choices=["auto", "h264_nvenc", "h264_amf", "libx264"], help="auto usa GPU NVIDIA/AMD se existir; libx264 usa CPU")
    p.add_argument(
        "--workers",
        type=int,
        choices=range(1, MAX_PARALLEL_WORKERS + 1),
        metavar="{1,2}",
        help="Quantidade de renders simultâneos; trava máxima segura: 2",
    )
    p.add_argument("--no-move", action="store_true", help="Não mover original para processados")
    p.add_argument("--no-history", action="store_true", help="Não gravar histórico desta fila")
    p.set_defaults(func=cmd_render_queue)

    p = sub.add_parser("watch", help="Monitora a pasta de entrada")
    p.add_argument("--polling", action="store_true", help="Usa monitoramento simples sem watchdog")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("benchmark", help="Testa ultra/pdv/balanced/quality com 1 vídeo explícito")
    p.add_argument("--input", required=True)
    p.add_argument("--preset")
    p.add_argument("--output-dir")
    p.add_argument("--codec", choices=["auto", "h264_nvenc", "h264_amf", "libx264"], help="auto, h264_nvenc, h264_amf ou libx264")
    p.set_defaults(func=cmd_benchmark)

    p = sub.add_parser("benchmark-next", help="Benchmark do próximo vídeo elegível respeitando *_AUTO e histórico")
    p.add_argument("--preset")
    p.add_argument("--output-dir")
    p.add_argument("--codec", choices=["auto", "h264_nvenc", "h264_amf", "libx264"], help="auto, h264_nvenc, h264_amf ou libx264")
    p.set_defaults(func=cmd_benchmark_next)

    p = sub.add_parser("history-clear", help="Limpa logs/render_history.json")
    p.set_defaults(func=cmd_history_clear)

    p = sub.add_parser("ui", help="Abre interface gráfica simples")
    p.set_defaults(func=cmd_ui)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("Interrompido pelo usuário.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
