package com.kkkkahn.klean.mobile;

import java.io.File;

public final class MediaItem {
    public final boolean live;
    public final String path;
    public final String thumbUri;
    public final String contentUri;
    public final String name;
    public long size;
    public final long dateMs;
    public final long durationMs;
    public final int width;
    public final int height;
    public boolean compressed;

    public MediaItem(
            boolean live,
            String path,
            String thumbUri,
            String contentUri,
            String name,
            long size,
            long dateMs,
            long durationMs,
            int width,
            int height) {
        this.live = live;
        this.path = path;
        this.thumbUri = thumbUri;
        this.contentUri = contentUri;
        this.name = name;
        this.size = size;
        this.dateMs = dateMs;
        this.durationMs = durationMs;
        this.width = width;
        this.height = height;
    }

    public File file() {
        return new File(path);
    }

    public String stem() {
        int dot = name.lastIndexOf('.');
        return dot > 0 ? name.substring(0, dot) : name;
    }
}
