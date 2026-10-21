package com.kkkkahn.klean.mobile;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;

/** Copies vivo's top-level vivoMediaExtInfo uuid box from source to encoded MP4. */
public final class Mp4BoxUtil {
    private static final byte[] VIVO_USER_TYPE = "vivoMediaExtInfo".getBytes(StandardCharsets.US_ASCII);
    private static final int MAX_UUID_BOX = 16 * 1024 * 1024;

    private Mp4BoxUtil() {
    }

    public static byte[] extractVivoUuid(File file) throws IOException {
        try (RandomAccessFile raf = new RandomAccessFile(file, "r")) {
            long length = raf.length();
            long position = 0;
            while (position + 8 <= length) {
                raf.seek(position);
                long size = readUInt32(raf);
                byte[] type = new byte[4];
                raf.readFully(type);
                int headerSize = 8;
                if (size == 1) {
                    if (position + 16 > length) break;
                    size = raf.readLong();
                    headerSize = 16;
                } else if (size == 0) {
                    size = length - position;
                }
                if (size < headerSize || position + size > length) break;
                if (type[0] == 'u' && type[1] == 'u' && type[2] == 'i' && type[3] == 'd') {
                    long payload = size - headerSize;
                    if (payload >= VIVO_USER_TYPE.length && size <= MAX_UUID_BOX) {
                        byte[] box = new byte[(int) size];
                        raf.seek(position);
                        raf.readFully(box);
                        boolean match = true;
                        for (int i = 0; i < VIVO_USER_TYPE.length; i++) {
                            if (box[headerSize + i] != VIVO_USER_TYPE[i]) {
                                match = false;
                                break;
                            }
                        }
                        if (match) return box;
                    }
                }
                position += size;
            }
        }
        return null;
    }

    /** Returns true when the source had a vivo uuid and the output now contains it. */
    public static boolean appendVivoUuid(File source, File output) throws IOException {
        byte[] box = extractVivoUuid(source);
        if (box == null) return false;
        byte[] existing = extractVivoUuid(output);
        if (java.util.Arrays.equals(box, existing)) return true;
        if (existing != null) {
            throw new IOException("输出已包含不同的 vivo uuid");
        }
        try (FileOutputStream stream = new FileOutputStream(output, true)) {
            stream.write(box);
            stream.flush();
            stream.getFD().sync();
        }
        return extractVivoUuid(output) != null;
    }

    private static long readUInt32(RandomAccessFile raf) throws IOException {
        return ((long) raf.readUnsignedByte() << 24)
                | ((long) raf.readUnsignedByte() << 16)
                | ((long) raf.readUnsignedByte() << 8)
                | raf.readUnsignedByte();
    }
}
