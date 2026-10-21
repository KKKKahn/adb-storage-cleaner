package com.kkkkahn.klean.mobile;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.MediaMetadataRetriever;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.util.LruCache;
import android.util.Size;
import android.widget.ImageView;

import java.io.File;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

final class ThumbnailLoader {
    private final Context context;
    private final LruCache<String, Bitmap> cache;
    private final ExecutorService executor = Executors.newFixedThreadPool(3);
    private final Handler main = new Handler(Looper.getMainLooper());

    ThumbnailLoader(Context context) {
        this.context = context.getApplicationContext();
        int max = (int) Math.min(48L * 1024 * 1024, Runtime.getRuntime().maxMemory() / 8);
        cache = new LruCache<String, Bitmap>(max) {
            @Override
            protected int sizeOf(String key, Bitmap value) {
                return value.getByteCount();
            }
        };
    }

    void load(MediaItem item, ImageView view) {
        String key = item.path;
        view.setTag(key);
        Bitmap cached = cache.get(key);
        if (cached != null && !cached.isRecycled()) {
            view.setImageBitmap(cached);
            return;
        }
        view.setImageDrawable(null);
        executor.execute(() -> {
            Bitmap bitmap = null;
            try {
                if (item.live) {
                    bitmap = decodeJpeg(new File(item.path.replaceAll("\\.[^.]+$", ".jpg")));
                }
                if (bitmap == null && item.thumbUri != null) {
                    bitmap = context.getContentResolver().loadThumbnail(
                            Uri.parse(item.thumbUri), new Size(240, 180), null);
                }
            } catch (Exception ignored) {
            }
            if (bitmap == null) return;
            Bitmap result = bitmap;
            cache.put(key, result);
            main.post(() -> {
                if (key.equals(view.getTag())) view.setImageBitmap(result);
            });
        });
    }

    void loadFile(String key, File file, ImageView view) {
        view.setTag(key);
        Bitmap cached = cache.get(key);
        if (cached != null && !cached.isRecycled()) {
            view.setImageBitmap(cached);
            return;
        }
        view.setImageDrawable(null);
        executor.execute(() -> {
            Bitmap bitmap = null;
            MediaMetadataRetriever retriever = new MediaMetadataRetriever();
            try {
                retriever.setDataSource(file.getAbsolutePath());
                bitmap = retriever.getFrameAtTime(0,
                        MediaMetadataRetriever.OPTION_CLOSEST_SYNC);
                if (bitmap != null && bitmap.getWidth() > 480) {
                    int width = 480;
                    int height = Math.max(1,
                            Math.round(bitmap.getHeight() * 480f / bitmap.getWidth()));
                    Bitmap scaled = Bitmap.createScaledBitmap(bitmap, width, height, true);
                    if (scaled != bitmap) bitmap.recycle();
                    bitmap = scaled;
                }
            } catch (Exception ignored) {
            } finally {
                try {
                    retriever.release();
                } catch (Exception ignored) {
                }
            }
            if (bitmap == null) return;
            Bitmap result = bitmap;
            cache.put(key, result);
            main.post(() -> {
                if (key.equals(view.getTag())) view.setImageBitmap(result);
            });
        });
    }

    private Bitmap decodeJpeg(File file) {
        if (!file.isFile()) return null;
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(file.getAbsolutePath(), bounds);
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null;
        int sample = 1;
        while (bounds.outWidth / (sample * 2) >= 240 && bounds.outHeight / (sample * 2) >= 180) {
            sample *= 2;
        }
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = sample;
        options.inPreferredConfig = Bitmap.Config.RGB_565;
        return BitmapFactory.decodeFile(file.getAbsolutePath(), options);
    }

    void shutdown() {
        executor.shutdownNow();
        cache.evictAll();
    }
}
