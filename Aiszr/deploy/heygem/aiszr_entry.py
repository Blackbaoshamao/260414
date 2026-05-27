#!/usr/bin/env python
# coding=utf-8
"""Unified Docker entry: batch (:8383) + streaming inference endpoints."""
import os
os.chdir('/code')

import gc
import json
import subprocess
import threading
import time
import traceback
from enum import Enum

import cv2
import numpy as np
from flask import Flask, Response, request
from service.config import result_dir, server_ip, server_port, temp_dir
from service.self_logger import logger
from service.trans_dh_service import (Status, TransDhTask, a, get_run_flag,
                                       init_p, task_dic, init_wh)

app = Flask(__name__)

# GPU lock: only one inference at a time (av_transfer uses shared task_info file)
_gpu_lock = threading.Lock()
_stream_sessions: dict = {}
_stream_session_seq = 0
_stream_lock = threading.Lock()


# ======================================================================
# Batch endpoints — verbatim from app_local.py
# ======================================================================

class EasyResponse:
    def __init__(self, code, success, msg, data: dict):
        self.code = code
        self.success = success
        self.msg = msg
        self.data = data


class ResponseCode(Enum):
    system_error = [9999, '系统异常']
    success = [10000, '成功']
    busy = [10001, '忙碌中']
    error1 = [10002, '参数异常']
    error2 = [10003, '获取锁异常']
    error3 = [10004, '任务不存在']


@app.route('/easy/submit', methods=['POST'])
def easy_submit():
    request_data = json.loads(request.data)
    _code = request_data['code']
    if not get_run_flag():
        logger.warning('%s -> busy ', _code)
        return json.dumps(
            EasyResponse(ResponseCode.busy.value[0], True, ResponseCode.busy.value[1], {}),
            default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)

    try:
        if 'audio_url' not in request_data or request_data['audio_url'] == '':
            return json.dumps(
                EasyResponse(ResponseCode.error1.value[0], False, 'audio_url参数缺失', {}),
                default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
        if 'video_url' not in request_data or request_data['video_url'] == '':
            return json.dumps(
                EasyResponse(ResponseCode.error1.value[0], False, 'video_url参数缺失', {}),
                default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
        if 'code' not in request_data or request_data['code'] == '':
            return json.dumps(
                EasyResponse(ResponseCode.error1.value[0], False, 'code参数缺失', {}),
                default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
        _audio_url = request_data['audio_url']
        _video_url = request_data['video_url']
        _code = request_data['code']

        if 'watermark_switch' not in request_data or request_data['watermark_switch'] == '':
            _watermark_switch = 0
        else:
            _watermark_switch = 1 if str(request_data['watermark_switch']) == '1' else 0
        if 'digital_auth' not in request_data or request_data['digital_auth'] == '':
            _digital_auth = 0
        else:
            _digital_auth = 1 if str(request_data['digital_auth']) == '1' else 0
        if 'chaofen' not in request_data or request_data['chaofen'] == '':
            _chaofen = 0
        else:
            _chaofen = 1 if str(request_data['chaofen']) == '1' else 0
        if 'pn' not in request_data or request_data['pn'] == '':
            _pn = 1
        else:
            _pn = 1 if str(request_data['pn']) == '1' else 0
        task = TransDhTask(_code, _audio_url, _video_url, _watermark_switch, _digital_auth, _chaofen, _pn)
        import threading as _th
        _th.Thread(target=task.work).start()
        return json.dumps(
            EasyResponse(ResponseCode.success.value[0], True, ResponseCode.success.value[0], {}),
            default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
    except Exception as e:
        traceback.print_exc()
        return json.dumps(
            EasyResponse(ResponseCode.system_error.value[0], False, ResponseCode.system_error.value[1], {}),
            default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
    finally:
        gc.collect()


@app.route('/easy/query', methods=['GET'])
def easy_query():
    del_flag = False
    get_data = request.args.to_dict()
    try:
        _code = get_data.get('code', '-1')
        if _code == '-1':
            return json.dumps(
                EasyResponse(ResponseCode.error1.value[0], False, 'code参数缺失', {}),
                default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
        task_progress = task_dic.get(_code, '-1')
        if task_progress != '-1':
            d = task_progress
            _status = d[0]
            _progress = d[1]
            _result = d[2]
            _msg = d[3]
            if _status == Status.run:
                return json.dumps(
                    EasyResponse(ResponseCode.success.value[0], True, '', {
                        'code': _code, 'status': _status.value, 'progress': _progress,
                        'result': _result, 'msg': _msg
                    }),
                    default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
            elif _status == Status.success:
                del_flag = True
                return json.dumps(
                    EasyResponse(ResponseCode.success.value[0], True, '', {
                        'code': _code, 'status': _status.value, 'progress': _progress,
                        'result': _result, 'msg': _msg, 'cost': d[4],
                        "video_duration": d[5], "width": d[6], "height": d[7]
                    }),
                    default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
            elif _status == Status.error:
                del_flag = True
                return json.dumps(
                    EasyResponse(ResponseCode.success.value[0], True, '', {
                        'code': _code, 'status': _status.value, 'progress': _progress,
                        'result': _result, 'msg': _msg
                    }),
                    default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
        else:
            return json.dumps(
                EasyResponse(ResponseCode.error3.value[0], True, ResponseCode.error3.value[1], {}),
                default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
    except Exception as e:
        traceback.print_exc()
        return json.dumps(
            EasyResponse(ResponseCode.system_error.value[0], False, ResponseCode.system_error.value[1], {}),
            default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)
    finally:
        if del_flag:
            try:
                del task_dic[_code]
            except Exception as e:
                traceback.print_exc()
                return json.dumps(
                    EasyResponse(ResponseCode.error3.value[0], True, ResponseCode.error3.value[1], {}),
                    default=lambda obj: obj.__dict__, sort_keys=True, ensure_ascii=False, indent=4)


# ======================================================================
# Streaming endpoints — Aiszr real-time lip-sync
# ======================================================================

@app.route('/aiszr/stream/start', methods=['POST'])
def aiszr_stream_start():
    data = json.loads(request.data)
    avatar_path = data.get('avatar_video_path', '')
    sample_rate = data.get('sample_rate', 24000)
    target_fps = data.get('target_fps', 25)

    if not avatar_path or not os.path.isfile(avatar_path):
        return json.dumps({'error': f'avatar not found: {avatar_path}'}), 400

    global _stream_session_seq
    with _stream_lock:
        _stream_session_seq += 1
        sid = f"s{_stream_session_seq}"

    # Run init_wh to set up face data in the worker process
    t0 = time.time()
    try:
        sim = init_wh(sid, avatar_path)
        logger.info("stream start init_wh sid={} sim={:.4f} ({:.1f}s)", sid, sim, time.time() - t0)
    except Exception as e:
        return json.dumps({'error': f'init_wh failed: {e}'}), 502

    # Detect face crop region from first frame
    cap = cv2.VideoCapture(avatar_path)
    ret, first_frame = cap.read()
    cap.release()

    crop_x, crop_y, crop_w, crop_h = 220, 540, 160, 96
    if ret:
        frame_h, frame_w = first_frame.shape[:2]
        try:
            from face_detect_utils.face_detect import FaceDetect
            fd = FaceDetect()
            faces = fd(first_frame)
            if faces:
                face = faces[0]
                landmarks = face[5] if len(face) > 5 else None
                bbox = face[:4] if len(face) >= 4 else None
                if bbox is not None:
                    x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                    face_w = x2 - x1
                    face_h = y2 - y1
                    mouth_y1 = y1 + int(face_h * 0.55)
                    mouth_y2 = y2
                    mouth_x1 = max(0, x1 - int(face_w * 0.1))
                    mouth_x2 = min(frame_w, x2 + int(face_w * 0.1))
                    crop_w = mouth_x2 - mouth_x1
                    crop_h = mouth_y2 - mouth_y1
                    crop_x = mouth_x1
                    crop_y = mouth_y1
        except Exception as e:
            logger.warning("face detect fallback: {}", e)

    _stream_sessions[sid] = {
        'avatar_path': avatar_path,
        'sample_rate': sample_rate,
        'target_fps': target_fps,
        'frame_id': 0,
        'crop_x': crop_x,
        'crop_y': crop_y,
        'crop_w': crop_w,
        'crop_h': crop_h,
        'chunk_seq': 0,
    }
    logger.info("aiszr stream start sid={} crop=({},{},{},{})", sid, crop_x, crop_y, crop_w, crop_h)

    return json.dumps({
        'session_id': sid,
        'crop_x': crop_x,
        'crop_y': crop_y,
        'crop_w': crop_w,
        'crop_h': crop_h,
    })


@app.route('/aiszr/stream/<sid>/infer', methods=['POST'])
def aiszr_stream_infer(sid: str):
    sess = _stream_sessions.get(sid)
    if sess is None:
        return json.dumps({'error': 'session not found'}), 404

    pcm = request.get_data()
    if not pcm:
        return Response(b'', mimetype='application/octet-stream')

    # Serialize: only one GPU inference at a time
    if not _gpu_lock.acquire(blocking=True, timeout=120):
        return json.dumps({'error': 'GPU busy'}), 503

    try:
        return _do_infer(sess, sid, pcm)
    finally:
        _gpu_lock.release()


def _do_infer(sess, sid, pcm):
    sess['chunk_seq'] += 1
    chunk_seq = sess['chunk_seq']
    sample_rate = sess.get('sample_rate', 24000)
    avatar_path = sess['avatar_path']

    chunk_code = f"{sid}_c{chunk_seq}"
    chunk_dir = os.path.join(temp_dir, chunk_code)
    os.makedirs(chunk_dir, exist_ok=True)

    audio_duration = len(pcm) / 2 / sample_rate

    import wave as wave_mod
    wav_path = os.path.join(chunk_dir, 'input.wav')
    with wave_mod.open(wav_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)

    target_fps = sess.get('target_fps', 25)
    needed_frames = max(1, int(audio_duration * target_fps))
    trimmed_path = os.path.join(chunk_dir, 'avatar_trimmed.mp4')
    subprocess.run([
        'ffmpeg', '-y', '-loglevel', 'error',
        '-i', avatar_path,
        '-vframes', str(needed_frames),
        '-c:v', 'libx264', '-crf', '18', '-an',
        trimmed_path,
    ], check=True, timeout=10)

    t0 = time.time()
    result_avi = os.path.join(chunk_dir, 'result.avi')
    task = TransDhTask(chunk_code, wav_path, trimmed_path, 0, 0, 0, 0)

    # Run batch in background thread. We hold _gpu_lock so no other request
    # can start until we release it (after work() finishes).
    batch_err = [None]

    def _run_batch():
        try:
            task.preprocess()
            task.work()
        except Exception as exc:
            batch_err[0] = exc
            logger.exception("batch failed for {}", chunk_code)

    batch_thread = threading.Thread(target=_run_batch, daemon=True)
    batch_thread.start()

    # Wait for result_info — av_transfer writes it after result.avi is complete
    result_info_path = '/code/result_info'
    for _ in range(2400):
        try:
            with open(result_info_path, 'r') as f:
                content = f.read().strip()
            if content and chunk_code in content:
                break
        except Exception:
            pass
        time.sleep(0.05)

    avi_elapsed = time.time() - t0

    # Read result.avi immediately before work() cleans it up
    crop_rgb = None
    if os.path.isfile(result_avi) and os.path.getsize(result_avi) > 0:
        cap = cv2.VideoCapture(result_avi)
        ret, result_frame = cap.read()
        cap.release()
        if ret:
            cx, cy = sess['crop_x'], sess['crop_y']
            cw, ch = sess['crop_w'], sess['crop_h']
            fh, fw = result_frame.shape[:2]
            crop = result_frame[max(0, cy):min(fh, cy + ch), max(0, cx):min(fw, cx + cw)]
            if crop.shape[:2] != (ch, cw):
                crop = cv2.resize(crop, (cw, ch))
            crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)

    # Wait for work() to finish before releasing GPU lock
    batch_thread.join(timeout=120)

    if crop_rgb is None:
        logger.warning("no crop for {} after {:.1f}s err={}", chunk_code, avi_elapsed, batch_err[0])
        return Response(b'', mimetype='application/octet-stream')

    logger.info("chunk {} avi={:.1f}s dur={:.1f}s ratio=1:{:.1f}",
                chunk_code, avi_elapsed, audio_duration, avi_elapsed / max(audio_duration, 0.1))
    sess['frame_id'] += 1

    try:
        import shutil
        shutil.rmtree(chunk_dir, ignore_errors=True)
    except Exception:
        pass

    return Response(crop_rgb.tobytes(), mimetype='application/octet-stream')


@app.route('/aiszr/stream/<sid>/stop', methods=['POST'])
def aiszr_stream_stop(sid: str):
    sess = _stream_sessions.pop(sid, None)
    if sess is not None:
        logger.info("aiszr stream stop sid={}", sid)
    return json.dumps({'ok': True})


@app.route('/aiszr/health', methods=['GET'])
def aiszr_health():
    return json.dumps({
        'ok': True,
        'streaming_sessions': len(_stream_sessions),
    })


# ======================================================================
# Main
# ======================================================================

if __name__ == '__main__':
    from face_detect_utils.face_detect import FaceDetect, pfpld
    import service.trans_dh_service as _tds
    _tds.FaceDetect = FaceDetect
    _tds.pfpld = pfpld

    # Optimize config for streaming: disable chaofen (super-resolution)
    import configparser
    _cfg = configparser.ConfigParser()
    _cfg.read('config/config.ini')
    if _cfg.getint('digital', 'chaofen', fallback=0) != 0:
        _cfg.set('digital', 'chaofen', '0')
        with open('config/config.ini', 'w') as _f:
            _cfg.write(_f)
        logger.info("disabled chaofen in config.ini for streaming performance")

    a()
    init_p()
    time.sleep(15)
    logger.info("******************* Unified entry (batch+stream) starting *******************")
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)
    if not os.path.exists(result_dir):
        os.makedirs(result_dir)

    app.run(host=str(server_ip), port=int(server_port), debug=False, threaded=True)
