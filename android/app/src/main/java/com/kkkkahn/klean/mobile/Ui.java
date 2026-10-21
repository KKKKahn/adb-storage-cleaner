package com.kkkkahn.klean.mobile;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

final class Ui {
    static final int BG = 0xFF080A12;
    static final int CARD = 0xFF121623;
    static final int CARD_2 = 0xFF191F31;
    static final int BORDER = 0xFF272E44;
    static final int TEXT = 0xFFF5F7FF;
    static final int MUTED = 0xFF929BB8;
    static final int ACCENT = 0xFF8B7CFF;
    static final int ACCENT_SOFT = 0x338B7CFF;
    static final int BLUE = 0xFF4F8CFF;
    static final int PINK = 0xFFF472B6;
    static final int OK = 0xFF4ADE80;
    static final int WARN = 0xFFFBBF24;
    static final int DANGER = 0xFFFF6B81;

    private static final String SANS = "sans-serif";
    private static final String MEDIUM = "sans-serif-medium";

    private Ui() {
    }

    static int dp(Context context, float value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    static Typeface regular() {
        return Typeface.create(SANS, Typeface.NORMAL);
    }

    static Typeface medium() {
        return Typeface.create(MEDIUM, Typeface.NORMAL);
    }

    static Typeface bold() {
        return Typeface.create(MEDIUM, Typeface.BOLD);
    }

    static GradientDrawable round(int color, float radiusDp, Context context) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(context, radiusDp));
        return drawable;
    }

    static GradientDrawable bordered(int color, float radiusDp, int stroke, Context context) {
        GradientDrawable drawable = round(color, radiusDp, context);
        drawable.setStroke(dp(context, 1), stroke);
        return drawable;
    }

    static GradientDrawable gradient(float radiusDp, Context context) {
        GradientDrawable drawable = new GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                new int[]{0xFF2F7BFF, 0xFF7C5CFF, 0xFFF472B6});
        drawable.setCornerRadius(dp(context, radiusDp));
        return drawable;
    }

    static TextView text(Context context, String value, float sizeSp, int color, boolean medium) {
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextSize(sizeSp);
        view.setTextColor(color);
        view.setTypeface(medium ? medium() : regular());
        view.setIncludeFontPadding(false);
        return view;
    }

    static TextView button(Context context, String value, boolean primary) {
        TextView view = text(context, value, 13f, primary ? Color.WHITE : TEXT, true);
        view.setGravity(Gravity.CENTER);
        view.setPadding(dp(context, 16), dp(context, 10), dp(context, 16), dp(context, 10));
        if (primary) {
            view.setBackground(gradient(10f, context));
        } else {
            view.setBackground(bordered(0x33191F31, 10f, BORDER, context));
        }
        view.setClickable(true);
        view.setFocusable(true);
        return view;
    }

    static ImageView logo(Context context, int sizeDp) {
        ImageView image = new ImageView(context);
        image.setImageResource(R.drawable.ic_bolt);
        image.setBackground(gradient(sizeDp * 0.28f, context));
        image.setPadding(dp(context, sizeDp * 0.22f), dp(context, sizeDp * 0.22f),
                dp(context, sizeDp * 0.22f), dp(context, sizeDp * 0.22f));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                dp(context, sizeDp), dp(context, sizeDp));
        image.setLayoutParams(params);
        return image;
    }

    static String bytes(long value) {
        if (value <= 0) return "0 B";
        String[] units = {"B", "KB", "MB", "GB", "TB"};
        double result = value;
        int index = 0;
        while (result >= 1000 && index < units.length - 1) {
            result /= 1000;
            index++;
        }
        return index == 0
                ? String.format(Locale.CHINA, "%.0f %s", result, units[index])
                : String.format(Locale.CHINA, "%.2f %s", result, units[index]);
    }

    static String duration(long ms) {
        if (ms <= 0) return "时长未知";
        double seconds = ms / 1000.0;
        if (seconds < 60) {
            return String.format(Locale.CHINA, "%.1f 秒", seconds);
        }
        int total = (int) Math.round(seconds);
        return String.format(Locale.CHINA, "%d:%02d", total / 60, total % 60);
    }

    static String date(long ms) {
        if (ms <= 0) return "日期未知";
        return new SimpleDateFormat("MM-dd HH:mm", Locale.CHINA).format(new Date(ms));
    }

    static void show(View view, boolean visible) {
        view.setVisibility(visible ? View.VISIBLE : View.GONE);
    }
}
