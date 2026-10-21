package com.kkkkahn.klean.mobile;

import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.media.MediaMetadataRetriever;
import android.media.MediaMuxer;
import android.os.Build;
import android.os.SystemClock;
import android.view.Surface;

import java.io.File;
import java.io.IOException;
import java.nio.ByteBuffer;

/**
 * Hardware H.264 transcode. Video goes decoder -> encoder surface; audio is
 * copied without re-encoding. The original file is never touched.
 */
public final class Transcoder {

    public interface Callback {
        void onProgress(float fraction, String phase);

        boolean isCancelled();
    }

    public static final class UnsupportedException extends Exception {
        public UnsupportedException(String message) {
            super(message);
        }
    }

    public static final class CancelledException extends Exception {
        public CancelledException() {
            super("已停止");
        }
    }

    public static final class Result {
        public long sourceBytes;
        public long outputBytes;
        public String sourceMd5 = "";
        public String outputMd5 = "";
        public long contentDurationMs;
        public long elapsedMs;
        public int width;
        public int height;
        public boolean hardware;
        public boolean uuidPreserved;
        public boolean profitable;
        public String encoderName = "";
    }

    private Transcoder() {
    }

    public static Result transcode(MediaItem item, File output, Callback callback)
            throws Exception {
        return transcode(item, output, 0.52d, callback);
    }

    public static Result transcode(
            MediaItem item, File output, double bitrateFactor, Callback callback)
            throws Exception {
        File source = item.file();
        if (!source.isFile()) {
            throw new UnsupportedException("源文件不存在");
        }
        if (output.exists() && !output.delete()) {
            throw new IOException("无法覆盖输出文件");
        }
        File parent = output.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) {
            throw new IOException("无法创建输出目录");
        }
        long startedAt = SystemClock.elapsedRealtime();
        long sourceBytes = source.length();
        Result result = new Result();
        result.sourceBytes = sourceBytes;
        result.sourceMd5 = Storage.md5(source);
        result.contentDurationMs = item.durationMs;

        MediaExtractor videoExtractor = new MediaExtractor();
        MediaExtractor audioExtractor = null;
        MediaCodec decoder = null;
        MediaCodec encoder = null;
        Surface inputSurface = null;
        MediaMuxer muxer = null;
        MediaMetadataRetriever retriever = null;
        boolean muxerStarted = false;
        try {
            videoExtractor.setDataSource(source.getAbsolutePath());
            int videoTrack = -1;
            MediaFormat inFormat = null;
            for (int i = 0; i < videoExtractor.getTrackCount(); i++) {
                MediaFormat format = videoExtractor.getTrackFormat(i);
                String mime = format.getString(MediaFormat.KEY_MIME);
                if (mime != null && mime.startsWith("video/")) {
                    videoTrack = i;
                    inFormat = format;
                    break;
                }
            }
            if (videoTrack < 0 || inFormat == null) {
                throw new UnsupportedException("没有视频流");
            }
            String inputMime = inFormat.getString(MediaFormat.KEY_MIME);
            if (inputMime == null || !(inputMime.equals("video/avc") || inputMime.equals("video/hevc"))) {
                throw new UnsupportedException("暂不支持 " + inputMime + "，仅处理 H.264 / HEVC");
            }
            int transfer = optInt(inFormat, MediaFormat.KEY_COLOR_TRANSFER, -1);
            if (transfer == MediaFormat.COLOR_TRANSFER_HLG
                    || transfer == MediaFormat.COLOR_TRANSFER_ST2084) {
                throw new UnsupportedException("HDR 视频已跳过");
            }
            int profile = optInt(inFormat, MediaFormat.KEY_PROFILE, -1);
            if (inputMime.equals("video/hevc") && profile >= 0 && (profile & 0x2) != 0) {
                throw new UnsupportedException("10-bit HEVC 已跳过");
            }
            if (inputMime.equals("video/avc") && profile >= 0 && (profile & 0x10) != 0) {
                throw new UnsupportedException("10-bit H.264 已跳过");
            }
            videoExtractor.selectTrack(videoTrack);
            int width = inFormat.getInteger(MediaFormat.KEY_WIDTH);
            int height = inFormat.getInteger(MediaFormat.KEY_HEIGHT);
            if (width <= 0 || height <= 0) {
                throw new UnsupportedException("无法读取分辨率");
            }
            width &= ~1;
            height &= ~1;
            result.width = width;
            result.height = height;
            float frameRate = 30f;
            if (inFormat.containsKey(MediaFormat.KEY_FRAME_RATE)) {
                try {
                    frameRate = inFormat.getFloat(MediaFormat.KEY_FRAME_RATE);
                } catch (ClassCastException ignored) {
                    frameRate = inFormat.getInteger(MediaFormat.KEY_FRAME_RATE);
                }
            }
            if (frameRate < 5f || frameRate > 120f) frameRate = 30f;
            int encoderFps = Math.round(Math.min(frameRate, 60f));
            long durationUs = optLong(inFormat, MediaFormat.KEY_DURATION, 0);
            if (durationUs <= 0) {
                retriever = new MediaMetadataRetriever();
                retriever.setDataSource(source.getAbsolutePath());
                String duration = retriever.extractMetadata(
                        MediaMetadataRetriever.METADATA_KEY_DURATION);
                if (duration != null) durationUs = Long.parseLong(duration) * 1000L;
            }
            if (durationUs > 0) result.contentDurationMs = durationUs / 1000L;
            int rotation = optInt(inFormat, MediaFormat.KEY_ROTATION, 0);
            if (rotation == 0) {
                if (retriever == null) {
                    retriever = new MediaMetadataRetriever();
                    retriever.setDataSource(source.getAbsolutePath());
                }
                String value = retriever.extractMetadata(
                        MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION);
                if (value != null) {
                    try {
                        rotation = Integer.parseInt(value);
                    } catch (NumberFormatException ignored) {
                        rotation = 0;
                    }
                }
            }

            long bitrate = optLong(inFormat, MediaFormat.KEY_BIT_RATE, 0);
            if (bitrate <= 0) {
                bitrate = Math.max(900_000L, Math.round(width * (double) height
                        * Math.max(frameRate, 24f) * 0.09d));
            }
            double factor = Math.max(0.30d, Math.min(0.78d, bitrateFactor));
            int targetBitrate = (int) Math.max(800_000L,
                    Math.min(40_000_000L, Math.round(bitrate * factor)));

            String encoderName = findHardwareEncoder("video/avc");
            if (encoderName != null) {
                encoder = MediaCodec.createByCodecName(encoderName);
                result.hardware = true;
                result.encoderName = encoderName;
            } else {
                encoder = MediaCodec.createEncoderByType("video/avc");
                result.hardware = false;
                result.encoderName = encoder.getName();
            }
            MediaFormat outFormat = MediaFormat.createVideoFormat("video/avc", width, height);
            outFormat.setInteger(MediaFormat.KEY_COLOR_FORMAT,
                    MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
            outFormat.setInteger(MediaFormat.KEY_BIT_RATE, targetBitrate);
            outFormat.setInteger(MediaFormat.KEY_FRAME_RATE, encoderFps);
            outFormat.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);
            try {
                MediaCodecInfo.CodecCapabilities capabilities =
                        encoder.getCodecInfo().getCapabilitiesForType("video/avc");
                if (capabilities.getEncoderCapabilities().isBitrateModeSupported(
                        MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR)) {
                    outFormat.setInteger(MediaFormat.KEY_BITRATE_MODE,
                            MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR);
                }
            } catch (Exception ignored) {
            }
            encoder.configure(outFormat, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            inputSurface = encoder.createInputSurface();
            decoder = MediaCodec.createDecoderByType(inputMime);
            decoder.configure(inFormat, inputSurface, null, 0);

            audioExtractor = new MediaExtractor();
            audioExtractor.setDataSource(source.getAbsolutePath());
            MediaFormat audioFormat = null;
            for (int i = 0; i < audioExtractor.getTrackCount(); i++) {
                MediaFormat format = audioExtractor.getTrackFormat(i);
                String mime = format.getString(MediaFormat.KEY_MIME);
                if (mime != null && mime.startsWith("audio/")) {
                    if (!mime.equals("audio/mp4a-latm") && !mime.equals("audio/3gpp")) {
                        throw new UnsupportedException("音频编码 " + mime + " 暂不支持");
                    }
                    audioExtractor.selectTrack(i);
                    audioFormat = format;
                    break;
                }
            }

            muxer = new MediaMuxer(output.getAbsolutePath(),
                    MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
            if (rotation == 90 || rotation == 180 || rotation == 270) {
                muxer.setOrientationHint(rotation);
            }
            int audioTrackIndex = audioFormat == null ? -1 : muxer.addTrack(audioFormat);

            encoder.start();
            decoder.start();
            MediaCodec.BufferInfo decoderInfo = new MediaCodec.BufferInfo();
            MediaCodec.BufferInfo encoderInfo = new MediaCodec.BufferInfo();
            int videoTrackIndex = -1;
            boolean inputDone = false;
            boolean outputDone = false;
            boolean encoderEosSignaled = false;
            long lastProgressAt = 0;
            while (!outputDone) {
                if (callback != null && callback.isCancelled()) {
                    throw new CancelledException();
                }
                if (!inputDone) {
                    int inputIndex = decoder.dequeueInputBuffer(10_000);
                    if (inputIndex >= 0) {
                        ByteBuffer buffer = decoder.getInputBuffer(inputIndex);
                        int sampleSize = buffer == null ? -1 : videoExtractor.readSampleData(buffer, 0);
                        if (sampleSize < 0) {
                            decoder.queueInputBuffer(inputIndex, 0, 0, 0,
                                    MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inputDone = true;
                        } else {
                            long pts = videoExtractor.getSampleTime();
                            decoder.queueInputBuffer(inputIndex, 0, sampleSize, pts, 0);
                            videoExtractor.advance();
                            long now = SystemClock.elapsedRealtime();
                            if (callback != null && durationUs > 0 && now - lastProgressAt > 120) {
                                callback.onProgress(
                                        Math.min(0.97f, pts / (float) durationUs),
                                        "硬件编码中");
                                lastProgressAt = now;
                            }
                        }
                    }
                }
                int decoderStatus = decoder.dequeueOutputBuffer(decoderInfo, 10_000);
                if (decoderStatus >= 0) {
                    decoder.releaseOutputBuffer(decoderStatus, true);
                    if ((decoderInfo.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0
                            && !encoderEosSignaled) {
                        encoder.signalEndOfInputStream();
                        encoderEosSignaled = true;
                    }
                }
                while (true) {
                    int encoderStatus = encoder.dequeueOutputBuffer(encoderInfo, 0);
                    if (encoderStatus == MediaCodec.INFO_TRY_AGAIN_LATER) {
                        break;
                    }
                    if (encoderStatus == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                        if (muxerStarted) {
                            throw new IOException("编码器重复改变格式");
                        }
                        videoTrackIndex = muxer.addTrack(encoder.getOutputFormat());
                        muxer.start();
                        muxerStarted = true;
                        continue;
                    }
                    if (encoderStatus < 0) continue;
                    ByteBuffer encoded = encoder.getOutputBuffer(encoderStatus);
                    if (encoded != null && encoderInfo.size > 0 && muxerStarted
                            && videoTrackIndex >= 0) {
                        encoded.position(encoderInfo.offset);
                        encoded.limit(encoderInfo.offset + encoderInfo.size);
                        muxer.writeSampleData(videoTrackIndex, encoded, encoderInfo);
                    }
                    boolean end = (encoderInfo.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                    encoder.releaseOutputBuffer(encoderStatus, false);
                    if (end) {
                        outputDone = true;
                        break;
                    }
                }
            }
            if (!muxerStarted) {
                throw new IOException("编码器没有产生输出");
            }
            if (audioTrackIndex >= 0) {
                if (callback != null) callback.onProgress(0.98f, "写入音频");
                copyAudio(audioExtractor, muxer, audioTrackIndex, callback);
            }
            if (callback != null) callback.onProgress(1f, "收尾");
        } catch (MediaCodec.CodecException | IllegalArgumentException error) {
            throw new UnsupportedException("硬件编码不可用：" + error.getMessage());
        } finally {
            if (muxer != null) {
                if (muxerStarted) {
                    try {
                        muxer.stop();
                    } catch (Exception ignored) {
                    }
                }
                try {
                    muxer.release();
                } catch (Exception ignored) {
                }
            }
            if (decoder != null) {
                try {
                    decoder.stop();
                } catch (Exception ignored) {
                }
                try {
                    decoder.release();
                } catch (Exception ignored) {
                }
            }
            if (encoder != null) {
                try {
                    encoder.stop();
                } catch (Exception ignored) {
                }
                try {
                    encoder.release();
                } catch (Exception ignored) {
                }
            }
            if (inputSurface != null) {
                try {
                    inputSurface.release();
                } catch (Exception ignored) {
                }
            }
            try {
                videoExtractor.release();
            } catch (Exception ignored) {
            }
            if (audioExtractor != null) {
                try {
                    audioExtractor.release();
                } catch (Exception ignored) {
                }
            }
            try {
                if (retriever != null) retriever.release();
            } catch (Exception ignored) {
            }
        }

        if (item.live) {
            boolean preserved = Mp4BoxUtil.appendVivoUuid(source, output);
            result.uuidPreserved = preserved;
            if (!preserved) {
                throw new UnsupportedException("缺少 vivo 实况标识，未生成结果");
            }
        } else {
            result.uuidPreserved = Mp4BoxUtil.appendVivoUuid(source, output);
        }
        verifyOutput(output);
        result.outputBytes = output.length();
        result.outputMd5 = Storage.md5(output);
        result.elapsedMs = Math.max(1, SystemClock.elapsedRealtime() - startedAt);
        result.profitable = result.outputBytes < sourceBytes;
        return result;
    }

    private static void copyAudio(
            MediaExtractor extractor,
            MediaMuxer muxer,
            int trackIndex,
            Callback callback) throws Exception {
        if (extractor == null) return;
        ByteBuffer buffer = ByteBuffer.allocateDirect(1024 * 1024);
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        while (true) {
            if (callback != null && callback.isCancelled()) {
                throw new CancelledException();
            }
            int size = extractor.readSampleData(buffer, 0);
            if (size < 0) break;
            info.set(0, size, extractor.getSampleTime(), extractor.getSampleFlags());
            muxer.writeSampleData(trackIndex, buffer, info);
            extractor.advance();
        }
    }

    private static void verifyOutput(File output) throws IOException {
        if (!output.isFile() || output.length() < 1024) {
            throw new IOException("输出文件为空");
        }
        MediaExtractor extractor = new MediaExtractor();
        try {
            extractor.setDataSource(output.getAbsolutePath());
            int videoTrack = -1;
            for (int i = 0; i < extractor.getTrackCount(); i++) {
                String mime = extractor.getTrackFormat(i).getString(MediaFormat.KEY_MIME);
                if (mime != null && mime.startsWith("video/")) {
                    videoTrack = i;
                    break;
                }
            }
            if (videoTrack < 0) throw new IOException("输出没有视频轨道");
            MediaFormat format = extractor.getTrackFormat(videoTrack);
            if (!format.containsKey(MediaFormat.KEY_WIDTH)
                    || format.getInteger(MediaFormat.KEY_WIDTH) <= 0) {
                throw new IOException("输出视频尺寸无效");
            }
        } finally {
            extractor.release();
        }
    }

    private static String findHardwareEncoder(String mime) {
        MediaCodecList list = new MediaCodecList(MediaCodecList.REGULAR_CODECS);
        for (MediaCodecInfo info : list.getCodecInfos()) {
            if (!info.isEncoder()) continue;
            if (Build.VERSION.SDK_INT >= 29 && !info.isHardwareAccelerated()) continue;
            for (String type : info.getSupportedTypes()) {
                if (type.equalsIgnoreCase(mime)) return info.getName();
            }
        }
        return null;
    }

    private static int optInt(MediaFormat format, String key, int fallback) {
        try {
            return format.containsKey(key) ? format.getInteger(key) : fallback;
        } catch (Exception ignored) {
            return fallback;
        }
    }

    private static long optLong(MediaFormat format, String key, long fallback) {
        try {
            return format.containsKey(key) ? format.getLong(key) : fallback;
        } catch (Exception ignored) {
            return fallback;
        }
    }
}
