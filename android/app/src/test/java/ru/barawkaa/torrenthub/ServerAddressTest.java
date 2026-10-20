package ru.barawkaa.torrenthub;

import org.junit.Test;
import static org.junit.Assert.*;

public class ServerAddressTest {
    @Test public void privateIpWithDefaultPort() {
        assertEquals("http://192.168.1.100:8766/",ServerAddress.normalize("192.168.1.100"));
    }
    @Test public void explicitPort() {
        assertEquals("http://192.168.1.100:9988/",ServerAddress.normalize("192.168.1.100:9988"));
    }
    @Test public void unusualTailscaleDnsLabel() {
        assertEquals("http://192-168-1-100-via1:8766/",ServerAddress.normalize("192-168-1-100-via1"));
    }
    @Test public void keeneticHostDefaultsToHttps() {
        assertEquals("https://torr.vapity.keenetic.link/",ServerAddress.normalize("torr.vapity.keenetic.link"));
    }
    @Test public void tailscaleNumericIp() {
        assertEquals("http://100.101.102.103:8766/",ServerAddress.normalize("100.101.102.103"));
    }
    @Test public void preservesUserSchemeAndPath() {
        assertEquals("http://box.local:8766/hub/",ServerAddress.normalize("http://box.local/hub"));
    }
    @Test public void rejectsDangerousScheme() {
        try {
            ServerAddress.normalize("javascript:alert(1)");
            fail("Must reject unknown schemes");
        } catch (IllegalArgumentException expected) {}
    }
    @Test public void rejectsCredentialsInUrl() {
        try {
            ServerAddress.normalize("http://user:password@192.168.1.100:8766");
            fail("Must reject URL credentials");
        } catch (IllegalArgumentException expected) {}
    }
    @Test public void rejectsMissingHost() {
        try { ServerAddress.normalize("http://"); fail("Expected rejection"); }
        catch (IllegalArgumentException expected) {}
    }
}
