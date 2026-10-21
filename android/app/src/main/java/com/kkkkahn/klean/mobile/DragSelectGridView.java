package com.kkkkahn.klean.mobile;

import android.content.Context;
import android.util.AttributeSet;
import android.view.HapticFeedbackConstants;
import android.view.MotionEvent;
import android.widget.GridView;

/** Long-press to start a gallery-style paint selection, then drag over cells. */
public final class DragSelectGridView extends GridView {
    public interface Listener {
        void onDragStart(int position);

        void onDragOver(int position);

        void onDragEnd();
    }

    private Listener listener;
    private boolean dragging;

    public DragSelectGridView(Context context) {
        super(context);
        init();
    }

    public DragSelectGridView(Context context, AttributeSet attrs) {
        super(context, attrs);
        init();
    }

    public DragSelectGridView(Context context, AttributeSet attrs, int defStyleAttr) {
        super(context, attrs, defStyleAttr);
        init();
    }

    private void init() {
        setOnItemLongClickListener((parent, view, position, id) -> {
            if (listener == null) return false;
            dragging = true;
            performHapticFeedback(HapticFeedbackConstants.LONG_PRESS);
            listener.onDragStart(position);
            return true;
        });
    }

    public void setDragListener(Listener listener) {
        this.listener = listener;
    }

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        if (!dragging) {
            return super.onTouchEvent(event);
        }
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_MOVE) {
            int height = getHeight();
            float y = event.getY();
            int step = Ui.dp(getContext(), 12);
            if (y < Ui.dp(getContext(), 64)) {
                scrollBy(0, -step);
            } else if (y > height - Ui.dp(getContext(), 64)) {
                scrollBy(0, step);
            }
            int position = pointToPosition((int) event.getX(), (int) event.getY());
            if (position >= 0 && listener != null) listener.onDragOver(position);
            return true;
        }
        if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) {
            dragging = false;
            if (listener != null) listener.onDragEnd();
            return true;
        }
        return true;
    }
}
