package com.kkkkahn.klean.mobile;

import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.Environment;
import android.provider.MediaStore;

import java.io.File;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

public final class MediaScanner {

    public interface Progress {
        void onProgress(String message);
    }

    public static final class Result {
        public final List<MediaItem> live = new ArrayList<>();
        public final List<MediaItem> videos = new ArrayList<>();
        public final List<MediaItem> all = new ArrayList<>();
        public long liveBytes;
        public long videoBytes;
    }

    private MediaScanner() {
    }

    public static Result scan(Context context, Progress progress) {
        ContentResolver resolver = context.getContentResolver();
        Result result = new Result();
        Map<String, ImageRow> images = new HashMap<>();

        if (progress != null) progress.onProgress("正在读取 JPEG 定格画面…");
        Uri imageUri = MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
        String[] imageColumns = {
                MediaStore.Images.Media._ID,
                MediaStore.Images.Media.DISPLAY_NAME,
                MediaStore.Images.Media.RELATIVE_PATH,
                MediaStore.Images.Media.DATA,
                MediaStore.Images.Media.SIZE,
                MediaStore.Images.Media.MIME_TYPE
        };
        try (Cursor cursor = resolver.query(
                imageUri,
                imageColumns,
                MediaStore.Images.Media.MIME_TYPE + "=?",
                new String[]{"image/jpeg"},
                null)) {
            if (cursor != null) {
                int idCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media._ID);
                int nameCol = cursor.getColumnIndexOrThrow(MediaStore.Images.Media.DISPLAY_NAME);
                int relativeCol = cursor.getColumnIndex(MediaStore.Images.Media.RELATIVE_PATH);
                int dataCol = cursor.getColumnIndex(MediaStore.Images.Media.DATA);
                int sizeCol = cursor.getColumnIndex(MediaStore.Images.Media.SIZE);
                while (cursor.moveToNext()) {
                    String name = cursor.getString(nameCol);
                    if (name == null) continue;
                    String path = readPath(cursor, name, relativeCol, dataCol);
                    if (path == null) continue;
                    long id = cursor.getLong(idCol);
                    Uri content = ContentUris.withAppendedId(imageUri, id);
                    ImageRow row = new ImageRow(path, content.toString(),
                            sizeCol >= 0 ? cursor.getLong(sizeCol) : 0);
                    images.put(pairKey(path), row);
                }
            }
        }

        if (progress != null) progress.onProgress("正在读取普通视频…");
        Uri videoUri = MediaStore.Video.Media.EXTERNAL_CONTENT_URI;
        String[] videoColumns = {
                MediaStore.Video.Media._ID,
                MediaStore.Video.Media.DISPLAY_NAME,
                MediaStore.Video.Media.RELATIVE_PATH,
                MediaStore.Video.Media.DATA,
                MediaStore.Video.Media.SIZE,
                MediaStore.Video.Media.DURATION,
                MediaStore.Video.Media.DATE_MODIFIED,
                MediaStore.Video.Media.WIDTH,
                MediaStore.Video.Media.HEIGHT,
                MediaStore.Video.Media.MIME_TYPE
        };
        try (Cursor cursor = resolver.query(
                videoUri,
                videoColumns,
                MediaStore.Video.Media.MIME_TYPE + "=?",
                new String[]{"video/mp4"},
                MediaStore.Video.Media.DATE_MODIFIED + " DESC")) {
            if (cursor == null) return result;
            int totalRows = cursor.getCount();
            int scanned = 0;
            int idCol = cursor.getColumnIndexOrThrow(MediaStore.Video.Media._ID);
            int nameCol = cursor.getColumnIndexOrThrow(MediaStore.Video.Media.DISPLAY_NAME);
            int relativeCol = cursor.getColumnIndex(MediaStore.Video.Media.RELATIVE_PATH);
            int dataCol = cursor.getColumnIndex(MediaStore.Video.Media.DATA);
            int sizeCol = cursor.getColumnIndex(MediaStore.Video.Media.SIZE);
            int durationCol = cursor.getColumnIndex(MediaStore.Video.Media.DURATION);
            int dateCol = cursor.getColumnIndex(MediaStore.Video.Media.DATE_MODIFIED);
            int widthCol = cursor.getColumnIndex(MediaStore.Video.Media.WIDTH);
            int heightCol = cursor.getColumnIndex(MediaStore.Video.Media.HEIGHT);
            while (cursor.moveToNext()) {
                scanned++;
                if (progress != null && scanned % 1000 == 0) {
                    progress.onProgress("正在读取普通视频… " + scanned + " / " + totalRows);
                }
                String name = cursor.getString(nameCol);
                if (name == null || name.startsWith(".")) continue;
                String path = readPath(cursor, name, relativeCol, dataCol);
                if (path == null || path.contains("/Movies/Klean/")
                        || path.contains("/Android/")) continue;
                long size = sizeCol >= 0 ? cursor.getLong(sizeCol) : 0;
                if (size <= 0) continue;
                long duration = durationCol >= 0 ? cursor.getLong(durationCol) : 0;
                long date = (dateCol >= 0 ? cursor.getLong(dateCol) : 0) * 1000L;
                int width = widthCol >= 0 ? cursor.getInt(widthCol) : 0;
                int height = heightCol >= 0 ? cursor.getInt(heightCol) : 0;
                long id = cursor.getLong(idCol);
                String content = ContentUris.withAppendedId(videoUri, id).toString();
                ImageRow pair = images.get(pairKey(path));
                boolean live = pair != null && duration > 0 && duration <= 5000;
                MediaItem item = new MediaItem(
                        live,
                        path,
                        live ? pair.uri : content,
                        content,
                        name,
                        size,
                        date,
                        duration,
                        width,
                        height);
                if (live) {
                    result.live.add(item);
                    result.liveBytes += size;
                } else {
                    result.videos.add(item);
                    result.videoBytes += size;
                }
                result.all.add(item);
            }
        }

        Collections.sort(result.all, (a, b) -> Long.compare(b.dateMs, a.dateMs));
        Collections.sort(result.live, (a, b) -> Long.compare(b.dateMs, a.dateMs));
        Collections.sort(result.videos, (a, b) -> Long.compare(b.dateMs, a.dateMs));
        return result;
    }

    private static String pairKey(String path) {
        File file = new File(path);
        String parent = file.getParent();
        String name = file.getName();
        int dot = name.lastIndexOf('.');
        String stem = dot > 0 ? name.substring(0, dot) : name;
        return (parent == null ? "" : parent) + "/" + stem.toLowerCase(Locale.ROOT);
    }

    private static String readPath(Cursor cursor, String name, int relativeCol, int dataCol) {
        if (dataCol >= 0 && !cursor.isNull(dataCol)) {
            String data = cursor.getString(dataCol);
            if (data != null && !data.isEmpty()) return data;
        }
        if (relativeCol >= 0 && !cursor.isNull(relativeCol)) {
            String relative = cursor.getString(relativeCol);
            if (relative != null) {
                return Environment.getExternalStorageDirectory().getAbsolutePath()
                        + "/" + relative + name;
            }
        }
        return null;
    }

    private static final class ImageRow {
        final String path;
        final String uri;
        final long size;

        ImageRow(String path, String uri, long size) {
            this.path = path;
            this.uri = uri;
            this.size = size;
        }
    }
}
