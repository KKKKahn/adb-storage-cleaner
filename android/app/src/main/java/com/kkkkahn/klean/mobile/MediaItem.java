package com.kkkkahn.klean.mobile;

import java.io.File;
import java.util.Calendar;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

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

    public long captureDay() {
        Calendar now = Calendar.getInstance();
        int maxYear = now.get(Calendar.YEAR) + 1;
        Matcher matcher = Pattern.compile("(\\d{4})(\\d{2})(\\d{2})_").matcher(name);
        if (matcher.find()) {
            int year = Integer.parseInt(matcher.group(1));
            if (year >= 1990 && year <= maxYear) {
                Calendar calendar = Calendar.getInstance();
                calendar.set(year, Integer.parseInt(matcher.group(2)) - 1,
                        Integer.parseInt(matcher.group(3)), 0, 0, 0);
                calendar.set(Calendar.MILLISECOND, 0);
                return calendar.getTimeInMillis();
            }
        }
        Calendar calendar = Calendar.getInstance();
        calendar.setTimeInMillis(dateMs > 0 ? dateMs : file().lastModified());
        int year = calendar.get(Calendar.YEAR);
        if (year < 1990 || year > maxYear) {
            calendar.setTimeInMillis(System.currentTimeMillis());
        }
        calendar.set(Calendar.HOUR_OF_DAY, 0);
        calendar.set(Calendar.MINUTE, 0);
        calendar.set(Calendar.SECOND, 0);
        calendar.set(Calendar.MILLISECOND, 0);
        return calendar.getTimeInMillis();
    }
}
