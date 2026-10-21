package com.kkkkahn.klean.mobile;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Outline;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewOutlineProvider;
import android.widget.CheckBox;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.recyclerview.widget.RecyclerView;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/** Month view: one full-width day header (with select-all) followed by that day's photos. */
final class MonthAdapter extends RecyclerView.Adapter<RecyclerView.ViewHolder> {
    interface Listener {
        void onSelectionChanged();
    }

    private static final int TYPE_HEADER = 0;
    private static final int TYPE_PHOTO = 1;
    private final Context context;
    private final ThumbnailLoader thumbnails;
    private final Set<String> selected;
    private final Map<String, TrialOutcome> outcomes;
    private final Listener listener;
    private final List<Row> rows = new ArrayList<>();
    private final Map<Long, Integer> headerPositions = new HashMap<>();

    MonthAdapter(Context context, ThumbnailLoader thumbnails, Set<String> selected,
                 Map<String, TrialOutcome> outcomes, Listener listener) {
        this.context = context;
        this.thumbnails = thumbnails;
        this.selected = selected;
        this.outcomes = outcomes;
        this.listener = listener;
    }

    void setItems(List<MediaItem> items) {
        rows.clear();
        headerPositions.clear();
        Map<Long, List<MediaItem>> days = new TreeMap<>(
                java.util.Collections.reverseOrder());
        for (MediaItem item : items) {
            days.computeIfAbsent(dayOf(item), value -> new ArrayList<>()).add(item);
        }
        for (Map.Entry<Long, List<MediaItem>> entry : days.entrySet()) {
            headerPositions.put(entry.getKey(), rows.size());
            rows.add(Row.header(entry.getKey(), entry.getValue()));
            for (MediaItem item : entry.getValue()) {
                rows.add(Row.photo(entry.getKey(), item));
            }
        }
        notifyDataSetChanged();
    }

    void refresh() {
        notifyDataSetChanged();
    }

    int spanSize(int position) {
        return rows.get(position).header ? 3 : 1;
    }

    @Override
    public int getItemViewType(int position) {
        return rows.get(position).header ? TYPE_HEADER : TYPE_PHOTO;
    }

    @Override
    public int getItemCount() {
        return rows.size();
    }

    @NonNull
    @Override
    public RecyclerView.ViewHolder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        if (viewType == TYPE_HEADER) {
            return buildHeader();
        }
        return buildPhoto();
    }

    @Override
    public void onBindViewHolder(@NonNull RecyclerView.ViewHolder holder, int position) {
        Row row = rows.get(position);
        if (row.header) bindHeader((HeaderHolder) holder, row);
        else bindPhoto((PhotoHolder) holder, row, position);
    }

    private HeaderHolder buildHeader() {
        LinearLayout root = new LinearLayout(context);
        root.setOrientation(LinearLayout.HORIZONTAL);
        root.setGravity(Gravity.CENTER_VERTICAL);
        root.setPadding(Ui.dp(context, 10), Ui.dp(context, 8),
                Ui.dp(context, 10), Ui.dp(context, 8));
        root.setBackground(Ui.round(Ui.CARD, 9f, context));
        RecyclerView.LayoutParams params = new RecyclerView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, Ui.dp(context, 7), 0, Ui.dp(context, 5));
        root.setLayoutParams(params);
        CheckBox check = new CheckBox(context);
        check.setClickable(false);
        check.setFocusable(false);
        check.setButtonTintList(new android.content.res.ColorStateList(
                new int[][]{
                        new int[]{android.R.attr.state_checked},
                        new int[]{}
                },
                new int[]{Ui.ACCENT, 0xFF59627C}));
        root.addView(check, new LinearLayout.LayoutParams(
                Ui.dp(context, 34), Ui.dp(context, 40)));
        LinearLayout texts = new LinearLayout(context);
        texts.setOrientation(LinearLayout.VERTICAL);
        root.addView(texts, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView title = Ui.text(context, "", 12.5f, Ui.TEXT, true);
        texts.addView(title);
        TextView meta = Ui.text(context, "", 9.5f, Ui.MUTED, false);
        meta.setPadding(0, Ui.dp(context, 2), 0, 0);
        texts.addView(meta);
        TextView select = Ui.text(context, "全选本日", 9.5f, Ui.ACCENT, true);
        select.setPadding(Ui.dp(context, 6), Ui.dp(context, 5),
                Ui.dp(context, 6), Ui.dp(context, 5));
        root.addView(select);
        HeaderHolder holder = new HeaderHolder(root);
        holder.check = check;
        holder.title = title;
        holder.meta = meta;
        holder.select = select;
        return holder;
    }

    private PhotoHolder buildPhoto() {
        LinearLayout root = new LinearLayout(context);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setClipToOutline(true);
        root.setOutlineProvider(new RoundedOutline(Ui.dp(context, 9)));
        RecyclerView.LayoutParams params = new RecyclerView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(Ui.dp(context, 3), Ui.dp(context, 3),
                Ui.dp(context, 3), Ui.dp(context, 3));
        root.setLayoutParams(params);
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
        TextView size = Ui.text(context, "", 8.5f, Color.WHITE, false);
        size.setPadding(Ui.dp(context, 4), Ui.dp(context, 1),
                Ui.dp(context, 4), Ui.dp(context, 1));
        size.setBackground(Ui.round(0x99000000, 4f, context));
        FrameLayout.LayoutParams sizeParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        sizeParams.gravity = Gravity.BOTTOM | Gravity.START;
        sizeParams.setMargins(Ui.dp(context, 6), 0, 0, Ui.dp(context, 6));
        imageWrap.addView(size, sizeParams);
        TextView outcome = Ui.text(context, "", 8.5f, Ui.OK, true);
        outcome.setPadding(Ui.dp(context, 5), Ui.dp(context, 2),
                Ui.dp(context, 5), Ui.dp(context, 2));
        FrameLayout.LayoutParams outcomeParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        outcomeParams.gravity = Gravity.BOTTOM | Gravity.END;
        outcomeParams.setMargins(0, 0, Ui.dp(context, 6), Ui.dp(context, 6));
        imageWrap.addView(outcome, outcomeParams);
        TextView time = Ui.text(context, "", 9f, Ui.MUTED, false);
        time.setPadding(Ui.dp(context, 7), Ui.dp(context, 5),
                Ui.dp(context, 7), Ui.dp(context, 7));
        root.addView(time);
        PhotoHolder holder = new PhotoHolder(root);
        holder.thumb = thumb;
        holder.check = check;
        holder.type = type;
        holder.size = size;
        holder.outcome = outcome;
        holder.time = time;
        return holder;
    }

    private void bindHeader(HeaderHolder holder, Row row) {
        int selectable = 0;
        int selectedCount = 0;
        long bytes = 0;
        for (MediaItem item : row.items) {
            bytes += item.size;
            if (selected.contains(item.path)) selectedCount++;
            if (!item.compressed) selectable++;
        }
        boolean all = selectable > 0 && selectedCount >= selectable;
        holder.check.setChecked(all);
        holder.check.setEnabled(selectable > 0);
        holder.title.setText(dayLabel(row.day));
        holder.meta.setText(String.format(Locale.CHINA,
                "%d 个 · %s%s", row.items.size(), Ui.bytes(bytes),
                selectedCount > 0 ? " · 已选 " + selectedCount : ""));
        holder.root.setOnClickListener(v -> {
            boolean everySelected = true;
            for (MediaItem item : row.items) {
                if (item.compressed) continue;
                if (!selected.contains(item.path)) {
                    everySelected = false;
                    break;
                }
            }
            for (MediaItem item : row.items) {
                if (item.compressed) continue;
                if (everySelected) selected.remove(item.path);
                else selected.add(item.path);
            }
            notifyDataSetChanged();
            listener.onSelectionChanged();
        });
    }

    private void bindPhoto(PhotoHolder holder, Row row, int position) {
        MediaItem item = row.item;
        boolean checked = selected.contains(item.path);
        holder.root.setBackground(Ui.bordered(
                checked ? 0xFF1B2038 : Ui.CARD,
                9f,
                checked ? Ui.ACCENT : Ui.BORDER,
                context));
        holder.check.setText(checked ? "✓" : "");
        holder.check.setBackground(checked
                ? circle(Ui.ACCENT)
                : borderedCircle(0x66000000, 0xCCFFFFFF));
        holder.type.setVisibility(View.GONE);
        holder.size.setText(Ui.bytes(item.size));
        holder.time.setText(timeLabel(item));
        holder.outcome.setVisibility(View.GONE);
        if (item.compressed) {
            holder.outcome.setVisibility(View.VISIBLE);
            holder.outcome.setText("已压缩");
            holder.outcome.setTextColor(Ui.ACCENT);
            holder.outcome.setBackground(Ui.round(0xCC241A45, 6f, context));
        } else {
            TrialOutcome result = outcomes.get(item.path);
            if (result != null) {
                holder.outcome.setVisibility(View.VISIBLE);
                if (!result.success) {
                    holder.outcome.setText("跳过");
                    holder.outcome.setTextColor(Ui.DANGER);
                    holder.outcome.setBackground(Ui.round(0xCC1A0F18, 6f, context));
                } else if (!result.profitable) {
                    holder.outcome.setText("未变小");
                    holder.outcome.setTextColor(Ui.WARN);
                    holder.outcome.setBackground(Ui.round(0xCC1A160B, 6f, context));
                } else {
                    holder.outcome.setText("待应用");
                    holder.outcome.setTextColor(Ui.OK);
                    holder.outcome.setBackground(Ui.round(0xCC0C2016, 6f, context));
                }
            }
        }
        holder.root.setContentDescription(
                (item.live ? "实况照片 " : "普通视频 ") + item.name);
        holder.thumb.setTag(item.path);
        holder.thumb.setImageDrawable(null);
        holder.thumb.setBackground(Ui.round(0xFF20263A, 0f, context));
        thumbnails.load(item, holder.thumb);
        holder.root.setOnClickListener(v -> {
            if (selected.contains(item.path)) selected.remove(item.path);
            else selected.add(item.path);
            notifyItemChanged(position);
            Integer header = headerPositions.get(row.day);
            if (header != null) notifyItemChanged(header);
            listener.onSelectionChanged();
        });
    }

    private static long dayOf(MediaItem item) {
        return item.captureDay();
    }

    private String dayLabel(long day) {
        Calendar calendar = Calendar.getInstance();
        calendar.setTimeInMillis(day);
        String[] weekdays = {"周日", "周一", "周二", "周三", "周四", "周五", "周六"};
        return new SimpleDateFormat("MM-dd", Locale.CHINA).format(calendar.getTime())
                + " " + weekdays[calendar.get(Calendar.DAY_OF_WEEK) - 1];
    }

    private String timeLabel(MediaItem item) {
        return new SimpleDateFormat("HH:mm", Locale.CHINA).format(
                new java.util.Date(item.dateMs > 0 ? item.dateMs : System.currentTimeMillis()));
    }

    private GradientDrawable circle(int color) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setShape(GradientDrawable.OVAL);
        drawable.setColor(color);
        return drawable;
    }

    private GradientDrawable borderedCircle(int fill, int stroke) {
        GradientDrawable drawable = circle(fill);
        drawable.setStroke(Ui.dp(context, 1), stroke);
        return drawable;
    }

    private static final class Row {
        final boolean header;
        final long day;
        final List<MediaItem> items;
        final MediaItem item;

        private Row(boolean header, long day, List<MediaItem> items, MediaItem item) {
            this.header = header;
            this.day = day;
            this.items = items;
            this.item = item;
        }

        static Row header(long day, List<MediaItem> items) {
            return new Row(true, day, items, null);
        }

        static Row photo(long day, MediaItem item) {
            return new Row(false, day, null, item);
        }
    }

    private static class HeaderHolder extends RecyclerView.ViewHolder {
        LinearLayout root;
        CheckBox check;
        TextView title;
        TextView meta;
        TextView select;

        HeaderHolder(View view) {
            super(view);
            root = (LinearLayout) view;
        }
    }

    private static class PhotoHolder extends RecyclerView.ViewHolder {
        LinearLayout root;
        ImageView thumb;
        TextView check;
        TextView type;
        TextView size;
        TextView outcome;
        TextView time;

        PhotoHolder(View view) {
            super(view);
            root = (LinearLayout) view;
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
