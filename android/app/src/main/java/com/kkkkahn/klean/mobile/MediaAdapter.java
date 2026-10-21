package com.kkkkahn.klean.mobile;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Outline;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewOutlineProvider;
import android.widget.BaseAdapter;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class MediaAdapter extends BaseAdapter {
    private static final Pattern NAME_DATE =
            Pattern.compile("(\\d{4})(\\d{2})(\\d{2})_(\\d{2})(\\d{2})(\\d{2})");
    private final Context context;
    private final ThumbnailLoader thumbnails;
    private final Set<String> selected;
    private final Map<String, TrialOutcome> outcomes;
    private final Runnable selectionChanged;
    private final List<MediaItem> items = new ArrayList<>();

    MediaAdapter(
            Context context,
            ThumbnailLoader thumbnails,
            Set<String> selected,
            Map<String, TrialOutcome> outcomes,
            Runnable selectionChanged) {
        this.context = context;
        this.thumbnails = thumbnails;
        this.selected = selected;
        this.outcomes = outcomes;
        this.selectionChanged = selectionChanged;
    }

    void setItems(List<MediaItem> next) {
        items.clear();
        items.addAll(next);
        notifyDataSetChanged();
    }

    void refresh() {
        notifyDataSetChanged();
    }

    @Override
    public int getCount() {
        return items.size();
    }

    @Override
    public MediaItem getItem(int position) {
        return items.get(position);
    }

    @Override
    public long getItemId(int position) {
        return items.get(position).path.hashCode();
    }

    @Override
    public View getView(int position, View convertView, ViewGroup parent) {
        Holder holder;
        if (convertView == null) {
            holder = createHolder();
            convertView = holder.root;
            convertView.setTag(holder);
        } else {
            holder = (Holder) convertView.getTag();
        }
        MediaItem item = getItem(position);
        boolean checked = selected.contains(item.path);
        holder.root.setBackground(bordered(
                checked ? 0xFF1B2038 : Ui.CARD,
                checked ? Ui.ACCENT : Ui.BORDER,
                checked ? 2 : 1));
        holder.time.setText(Ui.bytes(item.size));
        holder.meta.setText(dateText(item) + " " + timeText(item)
                + " · " + Ui.duration(item.durationMs));
        holder.type.setVisibility(View.GONE);
        holder.check.setVisibility(View.VISIBLE);
        holder.check.setText(checked ? "✓" : "");
        holder.check.setTextColor(Color.WHITE);
        holder.check.setBackground(checked
                ? circle(Ui.ACCENT)
                : borderedCircle(0x66000000, 0xCCFFFFFF));
        holder.outcome.setVisibility(View.GONE);
        TrialOutcome outcome = outcomes.get(item.path);
        if (item.compressed) {
            holder.outcome.setVisibility(View.VISIBLE);
            holder.outcome.setText("已压缩");
            holder.outcome.setTextColor(Ui.ACCENT);
            holder.outcome.setBackground(Ui.round(0xCC241A45, 6f, context));
        } else if (outcome != null) {
            holder.outcome.setVisibility(View.VISIBLE);
            if (!outcome.success) {
                holder.outcome.setText("跳过");
                holder.outcome.setTextColor(Ui.DANGER);
                holder.outcome.setBackground(Ui.round(0xCC1A0F18, 6f, context));
            } else if (!outcome.profitable) {
                holder.outcome.setText("未变小");
                holder.outcome.setTextColor(Ui.WARN);
                holder.outcome.setBackground(Ui.round(0xCC1A160B, 6f, context));
            } else {
                holder.outcome.setText("待应用");
                holder.outcome.setTextColor(Ui.OK);
                holder.outcome.setBackground(Ui.round(0xCC0C2016, 6f, context));
            }
        }
        holder.root.setContentDescription(
                (item.live ? "实况照片 " : "普通视频 ") + item.name);
        holder.thumb.setTag(item.path);
        holder.thumb.setImageDrawable(null);
        holder.thumb.setBackground(Ui.round(0xFF20263A, 0f, context));
        thumbnails.load(item, holder.thumb);
        return convertView;
    }

    private Holder createHolder() {
        LinearLayout root = new LinearLayout(context);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setClipToOutline(true);
        root.setOutlineProvider(new RoundedOutline(Ui.dp(context, 10)));

        FrameLayout imageWrap = new FrameLayout(context);
        root.addView(imageWrap, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        SquareImageView thumb = new SquareImageView(context);
        thumb.setScaleType(ImageView.ScaleType.CENTER_CROP);
        imageWrap.addView(thumb, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        TextView check = Ui.text(context, "", 12f, Color.WHITE, true);
        check.setGravity(Gravity.CENTER);
        FrameLayout.LayoutParams checkParams = new FrameLayout.LayoutParams(
                Ui.dp(context, 24), Ui.dp(context, 24));
        checkParams.gravity = Gravity.TOP | Gravity.START;
        checkParams.setMargins(Ui.dp(context, 6), Ui.dp(context, 6), 0, 0);
        imageWrap.addView(check, checkParams);

        TextView type = Ui.text(context, "实况", 9f, 0xFFD8CCFF, true);
        type.setPadding(Ui.dp(context, 5), Ui.dp(context, 2),
                Ui.dp(context, 5), Ui.dp(context, 2));
        FrameLayout.LayoutParams typeParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        typeParams.gravity = Gravity.TOP | Gravity.END;
        typeParams.setMargins(0, Ui.dp(context, 7), Ui.dp(context, 6), 0);
        imageWrap.addView(type, typeParams);

        TextView time = Ui.text(context, "", 8.5f, Color.WHITE, false);
        time.setPadding(Ui.dp(context, 4), Ui.dp(context, 1),
                Ui.dp(context, 4), Ui.dp(context, 1));
        time.setBackground(Ui.round(0x99000000, 4f, context));
        FrameLayout.LayoutParams timeParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        timeParams.gravity = Gravity.BOTTOM | Gravity.START;
        timeParams.setMargins(Ui.dp(context, 6), 0, 0, Ui.dp(context, 6));
        imageWrap.addView(time, timeParams);

        TextView outcome = Ui.text(context, "", 8.5f, Ui.OK, true);
        outcome.setPadding(Ui.dp(context, 5), Ui.dp(context, 2),
                Ui.dp(context, 5), Ui.dp(context, 2));
        FrameLayout.LayoutParams outcomeParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        outcomeParams.gravity = Gravity.BOTTOM | Gravity.END;
        outcomeParams.setMargins(0, 0, Ui.dp(context, 6), Ui.dp(context, 6));
        imageWrap.addView(outcome, outcomeParams);

        LinearLayout textWrap = new LinearLayout(context);
        textWrap.setOrientation(LinearLayout.VERTICAL);
        textWrap.setPadding(Ui.dp(context, 7), Ui.dp(context, 5),
                Ui.dp(context, 7), Ui.dp(context, 7));
        root.addView(textWrap, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView meta = Ui.text(context, "", 9f, Ui.MUTED, false);
        meta.setSingleLine(true);
        meta.setEllipsize(android.text.TextUtils.TruncateAt.END);
        textWrap.addView(meta);

        return new Holder(root, check, type, time, meta, outcome, thumb);
    }

    private String timeText(MediaItem item) {
        Matcher matcher = NAME_DATE.matcher(item.name);
        if (matcher.find()) {
            return matcher.group(4) + ":" + matcher.group(5);
        }
        return new SimpleDateFormat("HH:mm", Locale.CHINA).format(new Date(item.dateMs));
    }

    private String dateText(MediaItem item) {
        Matcher matcher = NAME_DATE.matcher(item.name);
        if (matcher.find()) {
            return matcher.group(2) + "-" + matcher.group(3);
        }
        return new SimpleDateFormat("MM-dd", Locale.CHINA).format(new Date(item.dateMs));
    }

    private android.graphics.drawable.GradientDrawable circle(int color) {
        android.graphics.drawable.GradientDrawable drawable =
                new android.graphics.drawable.GradientDrawable();
        drawable.setShape(android.graphics.drawable.GradientDrawable.OVAL);
        drawable.setColor(color);
        return drawable;
    }

    private android.graphics.drawable.GradientDrawable borderedCircle(int fill, int stroke) {
        android.graphics.drawable.GradientDrawable drawable = circle(fill);
        drawable.setStroke(Ui.dp(context, 1), stroke);
        return drawable;
    }

    private android.graphics.drawable.GradientDrawable bordered(int fill, int stroke, int width) {
        android.graphics.drawable.GradientDrawable drawable = Ui.round(fill, 10f, context);
        drawable.setStroke(Ui.dp(context, width), stroke);
        return drawable;
    }

    private static final class Holder {
        final LinearLayout root;
        final TextView check;
        final TextView type;
        final TextView time;
        final TextView meta;
        final TextView outcome;
        final ImageView thumb;

        Holder(LinearLayout root, TextView check, TextView type, TextView time,
               TextView meta, TextView outcome, ImageView thumb) {
            this.root = root;
            this.check = check;
            this.type = type;
            this.time = time;
            this.meta = meta;
            this.outcome = outcome;
            this.thumb = thumb;
        }
    }

    private static final class SquareImageView extends ImageView {
        SquareImageView(Context context) {
            super(context);
        }

        @Override
        protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
            super.onMeasure(widthMeasureSpec, widthMeasureSpec);
            int size = getMeasuredWidth();
            setMeasuredDimension(size, size);
        }
    }

    private static final class RoundedOutline extends ViewOutlineProvider {
        private final int radius;

        RoundedOutline(int radius) {
            this.radius = radius;
        }

        @Override
        public void getOutline(View view, Outline outline) {
            outline.setRoundRect(0, 0, view.getWidth(), view.getHeight(), radius);
        }
    }
}
