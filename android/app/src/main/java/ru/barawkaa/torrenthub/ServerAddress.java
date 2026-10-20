package ru.barawkaa.torrenthub;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;
import java.util.regex.Pattern;

/** Validates arbitrary user-supplied LAN, Tailscale and HTTPS proxy endpoints.
 * Does not perform DNS lookups: Tailscale hostnames may only resolve when VPN is on.
 */
public final class ServerAddress {
    private static final Pattern SCHEME = Pattern.compile("^[A-Za-z][A-Za-z0-9+.-]*://.*");
    private static final Pattern IPV4 = Pattern.compile("\\d{1,3}(\\.\\d{1,3}){3}");
    private ServerAddress() {}

    public static String normalize(String input) {
        if (input == null || input.trim().isEmpty()) {
            throw new IllegalArgumentException("Введите адрес сервера.");
        }
        String source = input.trim();
        if (source.indexOf(' ') >= 0 || source.indexOf('\n') >= 0 || source.indexOf('\t') >= 0) {
            throw new IllegalArgumentException("Убери пробелы из адреса.");
        }
        boolean explicitScheme = SCHEME.matcher(source).matches();
        if (!explicitScheme && source.contains("://")) {
            throw new IllegalArgumentException("Поддерживаются только http:// и https://");
        }
        if (!explicitScheme) {
            String host = source.split("[/:?#]", 2)[0].toLowerCase(Locale.ROOT);
            boolean local = isLocal(host);
            source = (local ? "http://" : "https://") + source;
        }
        try {
            URI uri = new URI(source);
            String scheme = uri.getScheme();
            if (!"http".equalsIgnoreCase(scheme) && !"https".equalsIgnoreCase(scheme)) {
                throw new IllegalArgumentException("Разрешены только HTTP и HTTPS.");
            }
            if (uri.getHost() == null || uri.getHost().isEmpty()) {
                throw new IllegalArgumentException("Некорректное имя устройства или домен.");
            }
            if (uri.getUserInfo() != null) {
                throw new IllegalArgumentException("Не вставляй пароль в URL.");
            }
            int port = uri.getPort();
            if (port > 65535 || port == 0 || port < -1) {
                throw new IllegalArgumentException("Порт должен быть от 1 до 65535.");
            }
            String host = uri.getHost().toLowerCase(Locale.ROOT);
            if (port == -1 && isLocal(host)) port = 8766;
            String path = uri.getRawPath();
            if (path == null || path.isEmpty()) path = "/";
            if (!path.endsWith("/") && !path.contains(".")) path += "/";
            URI clean = new URI(scheme.toLowerCase(Locale.ROOT), null, host, port,
                    path, uri.getRawQuery(), null);
            return clean.toASCIIString();
        } catch (URISyntaxException e) {
            throw new IllegalArgumentException("Некорректный адрес. Пример: 192.168.1.100:8766", e);
        }
    }

    private static boolean isLocal(String h) {
        return IPV4.matcher(h).matches() || h.indexOf('.') == -1
                || h.endsWith(".local") || h.endsWith(".lan")
                || h.endsWith(".home.arpa") || h.endsWith(".internal");
    }
}
