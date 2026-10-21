package com.kkkkahn.klean.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import android.media.MediaScannerConnection;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.HashSet;

/** History, backup batches and the safe backup -> replace -> restore pipeline. */
final class Storage {
    private static final String PREFS = "klean_history";
    private static final String KEY = "entries";
    private static final String MANIFEST = "manifest.json";
    private static final char[] HEX = "0123456789abcdef".toCharArray();

    private Storage() {
    }

    static final class HistoryEntry {
        long originalSize;
        long compressedSize;
        String originalMd5 = "";
        String compressedMd5 = "";
        String preset = "";
        String batch = "";
        String backup = "";
        long mtime;

        JSONObject toJson() throws JSONException {
            JSONObject object = new JSONObject();
            object.put("original_size", originalSize);
            object.put("compressed_size", compressedSize);
            object.put("original_md5", originalMd5);
            object.put("compressed_md5", compressedMd5);
            object.put("preset", preset);
            object.put("batch", batch);
            object.put("backup", backup);
            object.put("mtime", mtime);
            return object;
        }

        static HistoryEntry fromJson(JSONObject object) {
            HistoryEntry entry = new HistoryEntry();
            entry.originalSize = object.optLong("original_size");
            entry.compressedSize = object.optLong("compressed_size");
            entry.originalMd5 = object.optString("original_md5");
            entry.compressedMd5 = object.optString("compressed_md5");
            entry.preset = object.optString("preset");
            entry.batch = object.optString("batch");
            entry.backup = object.optString("backup");
            entry.mtime = object.optLong("mtime");
            return entry;
        }
    }

    static final class BackupRecord {
        String path = "";
        String jpg = "";
        String backup = "";
        String originalMd5 = "";
        String compressedMd5 = "";
        String preset = "";
        long originalSize;
        long compressedSize;
        long mtime;
        boolean live;
        File batchDir;

        File file() {
            return batchDir == null ? null : new File(batchDir, backup);
        }

        JSONObject toJson() throws JSONException {
            JSONObject object = new JSONObject();
            object.put("path", path);
            object.put("jpg", jpg);
            object.put("backup", backup);
            object.put("original_md5", originalMd5);
            object.put("compressed_md5", compressedMd5);
            object.put("preset", preset);
            object.put("original_size", originalSize);
            object.put("compressed_size", compressedSize);
            object.put("mtime", mtime);
            object.put("live", live);
            return object;
        }

        static BackupRecord fromJson(JSONObject object, File batchDir) {
            BackupRecord record = new BackupRecord();
            record.path = object.optString("path");
            record.jpg = object.optString("jpg");
            record.backup = object.optString("backup");
            record.originalMd5 = object.optString("original_md5");
            record.compressedMd5 = object.optString("compressed_md5");
            record.preset = object.optString("preset");
            record.originalSize = object.optLong("original_size");
            record.compressedSize = object.optLong("compressed_size");
            record.mtime = object.optLong("mtime");
            record.live = object.optBoolean("live");
            record.batchDir = batchDir;
            return record;
        }
    }

    static final class BackupBatch {
        File dir;
        String id = "";
        long createdAt;
        final List<BackupRecord> entries = new ArrayList<>();

        long bytes() {
            long total = 0;
            for (BackupRecord record : entries) total += record.originalSize;
            return total;
        }
    }

    static File backupRoot(Context context) {
        File root = context.getExternalFilesDir("backups");
        if (root == null) root = new File(context.getFilesDir(), "backups");
        if (!root.exists()) root.mkdirs();
        return root;
    }

    static File jobsRoot(Context context) {
        File root = context.getExternalFilesDir("jobs");
        if (root == null) root = new File(context.getFilesDir(), "jobs");
        if (!root.exists()) root.mkdirs();
        return root;
    }

    static File newBatch(Context context) {
        File root = backupRoot(context);
        String stamp = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(new Date());
        File dir = new File(root, stamp);
        int counter = 2;
        while (dir.exists()) {
            dir = new File(root, stamp + "-" + counter);
            counter++;
        }
        dir.mkdirs();
        return dir;
    }

    static File newJob(Context context) {
        File root = jobsRoot(context);
        String stamp = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(new Date());
        File dir = new File(root, "job-" + stamp);
        int counter = 2;
        while (dir.exists()) {
            dir = new File(root, "job-" + stamp + "-" + counter);
            counter++;
        }
        dir.mkdirs();
        return dir;
    }

    static void clearOldJobs(Context context) {
        File root = jobsRoot(context);
        File[] children = root.listFiles();
        if (children == null) return;
        for (File child : children) deleteTree(child);
    }

    static void deleteTree(File file) {
        if (file == null || !file.exists()) return;
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) for (File child : children) deleteTree(child);
        }
        file.delete();
    }

    static String relativeToSdcard(String path) {
        String relative = path.startsWith("/sdcard/")
                ? path.substring("/sdcard/".length())
                : path.startsWith("/storage/emulated/0/")
                ? path.substring("/storage/emulated/0/".length())
                : path.replaceFirst("^/+", "");
        if (relative.contains("..")) {
            throw new IllegalArgumentException("非法媒体路径");
        }
        return relative;
    }

    static File backupTarget(File batchDir, String remotePath) {
        return new File(batchDir, "sdcard/" + relativeToSdcard(remotePath));
    }

    static String md5(File file) throws IOException {
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("MD5");
        } catch (Exception error) {
            throw new IOException("MD5 不可用", error);
        }
        try (InputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[1024 * 1024];
            int count;
            while ((count = input.read(buffer)) > 0) digest.update(buffer, 0, count);
        }
        byte[] bytes = digest.digest();
        char[] result = new char[bytes.length * 2];
        for (int i = 0; i < bytes.length; i++) {
            int value = bytes[i] & 0xff;
            result[i * 2] = HEX[value >>> 4];
            result[i * 2 + 1] = HEX[value & 0x0f];
        }
        return new String(result);
    }

    static void copy(File source, File target) throws IOException {
        File parent = target.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) {
            throw new IOException("无法创建目录 " + parent);
        }
        try (InputStream input = new FileInputStream(source);
             OutputStream output = new FileOutputStream(target)) {
            byte[] buffer = new byte[1024 * 1024];
            int count;
            while ((count = input.read(buffer)) > 0) output.write(buffer, 0, count);
            output.flush();
        }
    }

    static void syncCopy(File source, File target) throws IOException {
        File parent = target.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) {
            throw new IOException("无法创建目录 " + parent);
        }
        try (FileInputStream input = new FileInputStream(source);
             FileOutputStream output = new FileOutputStream(target)) {
            byte[] buffer = new byte[1024 * 1024];
            int count;
            while ((count = input.read(buffer)) > 0) output.write(buffer, 0, count);
            output.getFD().sync();
        }
    }

    static void atomicReplace(File source, File target) throws IOException {
        try {
            Files.move(source.toPath(), target.toPath(),
                    StandardCopyOption.REPLACE_EXISTING,
                    StandardCopyOption.ATOMIC_MOVE);
        } catch (AtomicMoveNotSupportedException error) {
            Files.move(source.toPath(), target.toPath(),
                    StandardCopyOption.REPLACE_EXISTING);
        }
        target.setReadable(true, false);
        target.setWritable(true, false);
    }

    static void scan(Context context, File file) {
        MediaScannerConnection.scanFile(
                context, new String[]{file.getAbsolutePath()},
                new String[]{"video/mp4"}, null);
    }

    // --------------------------------------------------------------- history

    static synchronized Map<String, HistoryEntry> loadHistory(Context context) {
        Map<String, HistoryEntry> result = new LinkedHashMap<>();
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String raw = prefs.getString(KEY, "{}");
        try {
            JSONObject object = new JSONObject(raw);
            Iterator<String> keys = object.keys();
            while (keys.hasNext()) {
                String path = keys.next();
                JSONObject value = object.optJSONObject(path);
                if (value != null) result.put(path, HistoryEntry.fromJson(value));
            }
        } catch (JSONException ignored) {
        }
        return result;
    }

    static synchronized void saveHistory(Context context, Map<String, HistoryEntry> history) {
        JSONObject object = new JSONObject();
        try {
            for (Map.Entry<String, HistoryEntry> entry : history.entrySet()) {
                object.put(entry.getKey(), entry.getValue().toJson());
            }
        } catch (JSONException ignored) {
        }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putString(KEY, object.toString()).apply();
    }

    static synchronized void putHistory(Context context, String path, HistoryEntry entry) {
        Map<String, HistoryEntry> history = loadHistory(context);
        history.put(path, entry);
        saveHistory(context, history);
    }

    static synchronized void removeHistory(Context context, String path) {
        Map<String, HistoryEntry> history = loadHistory(context);
        if (history.remove(path) != null) saveHistory(context, history);
    }

    // --------------------------------------------------------------- backups

    static void writeManifest(File batchDir, List<BackupRecord> records) {
        JSONObject object = new JSONObject();
        try {
            object.put("id", batchDir.getName());
            object.put("created_at", batchDir.lastModified());
            JSONArray array = new JSONArray();
            for (BackupRecord record : records) array.put(record.toJson());
            object.put("entries", array);
            File target = new File(batchDir, MANIFEST);
            File temp = new File(batchDir, MANIFEST + ".tmp");
            try (FileOutputStream output = new FileOutputStream(temp)) {
                output.write(object.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
                output.getFD().sync();
            }
            Files.move(temp.toPath(), target.toPath(),
                    StandardCopyOption.REPLACE_EXISTING);
        } catch (Exception ignored) {
        }
    }

    static List<BackupBatch> loadBackups(Context context) {
        List<BackupBatch> batches = new ArrayList<>();
        File root = backupRoot(context);
        File[] dirs = root.listFiles();
        if (dirs == null) return batches;
        for (File dir : dirs) {
            if (!dir.isDirectory()) continue;
            File manifest = new File(dir, MANIFEST);
            if (!manifest.isFile()) continue;
            try {
                String raw = new String(Files.readAllBytes(manifest.toPath()),
                        java.nio.charset.StandardCharsets.UTF_8);
                JSONObject object = new JSONObject(raw);
                JSONArray array = object.optJSONArray("entries");
                if (array == null) continue;
                BackupBatch batch = new BackupBatch();
                batch.dir = dir;
                batch.id = dir.getName();
                batch.createdAt = object.optLong("created_at", dir.lastModified());
                for (int i = 0; i < array.length(); i++) {
                    JSONObject value = array.optJSONObject(i);
                    if (value == null) continue;
                    BackupRecord record = BackupRecord.fromJson(value, dir);
                    File backup = record.file();
                    if (backup != null && backup.isFile() && backup.length() > 0) {
                        batch.entries.add(record);
                    }
                }
                if (!batch.entries.isEmpty()) {
                    Collections.sort(batch.entries,
                            (a, b) -> Long.compare(b.mtime, a.mtime));
                    batches.add(batch);
                }
            } catch (Exception ignored) {
            }
        }
        Collections.sort(batches, (a, b) -> b.id.compareTo(a.id));
        return batches;
    }

    static void deleteBackups(Context context, List<BackupRecord> records) {
        Set<File> touched = new HashSet<>();
        for (BackupRecord record : records) {
            File file = record.file();
            if (file != null && file.isFile()) file.delete();
            if (record.batchDir != null) touched.add(record.batchDir);
        }
        for (File batchDir : touched) {
            List<BackupRecord> remaining = new ArrayList<>();
            List<BackupBatch> batches = loadBackups(context);
            for (BackupBatch batch : batches) {
                if (batch.dir.equals(batchDir)) remaining.addAll(batch.entries);
            }
            if (remaining.isEmpty()) {
                deleteTree(batchDir);
            } else {
                writeManifest(batchDir, remaining);
            }
        }
    }

    // --------------------------------------------------------------- apply

    static BackupRecord apply(
            Context context,
            MediaItem item,
            File compressed,
            String expectedSourceMd5,
            String expectedOutputMd5,
            long compressedSize,
            File batchDir,
            String preset) throws IOException {
        File target = item.file();
        if (!target.isFile()) throw new IOException("手机文件已不存在");
        if (!compressed.isFile()) throw new IOException("压缩结果不存在");
        if (!expectedOutputMd5.isEmpty() && !expectedOutputMd5.equals(md5(compressed))) {
            throw new IOException("压缩文件 MD5 已变化");
        }
        String current = md5(target);
        if (current.equals(expectedOutputMd5)) {
            BackupRecord record = new BackupRecord();
            record.path = item.path;
            record.originalSize = target.length();
            record.compressedSize = compressedSize;
            record.originalMd5 = expectedSourceMd5;
            record.compressedMd5 = expectedOutputMd5;
            record.live = item.live;
            record.preset = preset;
            record.mtime = target.lastModified();
            return record;
        }
        if (!current.equals(expectedSourceMd5)) {
            throw new IOException("手机文件在压缩后发生变化，已跳过");
        }
        long originalSize = target.length();
        long mtime = target.lastModified();
        File backup = backupTarget(batchDir, item.path);
        if (backup.exists()) backup.delete();
        syncCopy(target, backup);
        if (!expectedSourceMd5.equals(md5(backup))) {
            backup.delete();
            throw new IOException("原片备份校验失败");
        }
        File temp = new File(target.getParentFile(),
                ".klean-" + target.getName() + "-" + System.nanoTime() + ".tmp");
        try {
            syncCopy(compressed, temp);
            if (!expectedOutputMd5.isEmpty() && !expectedOutputMd5.equals(md5(temp))) {
                throw new IOException("临时文件 MD5 不一致");
            }
            atomicReplace(temp, target);
            if (!expectedOutputMd5.isEmpty() && !expectedOutputMd5.equals(md5(target))) {
                throw new IOException("替换后 MD5 不一致");
            }
            target.setLastModified(mtime);
            scan(context, target);
        } catch (Exception error) {
            temp.delete();
            try {
                if (backup.isFile()) {
                    File restoreTemp = new File(target.getParentFile(),
                            ".klean-restore-" + target.getName() + "-" + System.nanoTime() + ".tmp");
                    syncCopy(backup, restoreTemp);
                    atomicReplace(restoreTemp, target);
                    scan(context, target);
                }
            } catch (Exception ignored) {
            }
            throw error instanceof IOException
                    ? (IOException) error
                    : new IOException(error.getMessage(), error);
        }
        BackupRecord record = new BackupRecord();
        record.path = item.path;
        record.jpg = item.live ? item.path.replaceAll("\\.[^.]+$", ".jpg") : "";
        record.backup = batchDir.toPath().relativize(backup.toPath()).toString();
        record.originalSize = originalSize;
        record.compressedSize = compressedSize;
        record.originalMd5 = expectedSourceMd5;
        record.compressedMd5 = expectedOutputMd5;
        record.mtime = mtime;
        record.live = item.live;
        record.preset = preset;
        record.batchDir = batchDir;
        return record;
    }

    static void restore(Context context, BackupRecord record) throws IOException {
        File backup = record.file();
        if (backup == null || !backup.isFile()) {
            throw new IOException("备份文件已丢失");
        }
        if (!record.originalMd5.equals(md5(backup))) {
            throw new IOException("备份文件校验失败");
        }
        File target = new File(record.path);
        String current = target.isFile() ? md5(target) : "";
        if (current.equals(record.originalMd5)) {
            removeHistory(context, record.path);
            return;
        }
        if (!record.compressedMd5.isEmpty() && !current.equals(record.compressedMd5)) {
            throw new IOException("手机文件已变化，跳过恢复");
        }
        File parent = target.getParentFile();
        if (parent != null && !parent.exists()) parent.mkdirs();
        File temp = new File(parent,
                ".klean-restore-" + target.getName() + "-" + System.nanoTime() + ".tmp");
        try {
            syncCopy(backup, temp);
            atomicReplace(temp, target);
            if (!record.originalMd5.equals(md5(target))) {
                throw new IOException("恢复后 MD5 不一致");
            }
            if (record.mtime > 0) target.setLastModified(record.mtime);
            scan(context, target);
        } finally {
            temp.delete();
        }
        removeHistory(context, record.path);
    }
}
