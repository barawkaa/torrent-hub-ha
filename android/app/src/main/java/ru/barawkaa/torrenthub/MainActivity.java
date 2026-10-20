package ru.barawkaa.torrenthub;

import android.Manifest;
import android.app.Activity;
import android.app.DownloadManager;
import android.app.UiModeManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.URLUtil;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Native host for one shared Home Assistant Torrent Hub web UI.
 *
 * There is no embedded copy of the web site and no hardcoded remote service:
 * each user chooses the server and can change it at any time. Uses Android
 * System WebView so the only updateable web engine is the OS WebView.
 */
public final class MainActivity extends Activity {
    private static final int COLOR_BG = Color.rgb(10, 19, 32);
    private static final int COLOR_PANEL = Color.rgb(17, 34, 52);
    private static final int COLOR_TEXT = Color.rgb(235, 246, 255);
    private static final int COLOR_MUTED = Color.rgb(159, 185, 205);
    private static final int COLOR_ACCENT = Color.rgb(0, 174, 166);
    private static final String DEFAULT_ADDRESS = "192.168.1.100:8766";
    private static final String PREFS = "torrent_hub_servers";
    private static final int REQUEST_DOWNLOAD = 88;

    private FrameLayout root;
    private WebView web;
    private ProgressBar progress;
    private EditText addressField;
    private EditText nameField;
    private Spinner chooser;
    private final List<ServerProfile> profiles = new ArrayList<>();
    private SharedPreferences prefs;
    private String currentUrl = "";
    private String pendingDownloadUrl, pendingDownloadAgent, pendingDownloadDisposition,
            pendingDownloadType;
    private int selectedIndex = -1;
    private boolean showingSetup = true;
    private boolean isTv;

    private static final class ServerProfile {
        String name, url;
        ServerProfile(String name, String url) { this.name=name; this.url=url; }
    }

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        isTv = ((UiModeManager) getSystemService(Context.UI_MODE_SERVICE))
                .getCurrentModeType() == Configuration.UI_MODE_TYPE_TELEVISION;
        if (isTv) {
            getWindow().getDecorView().setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION |
                    View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
        }
        getWindow().setStatusBarColor(COLOR_BG);
        getWindow().setNavigationBarColor(COLOR_BG);
        root = new FrameLayout(this);
        root.setBackgroundColor(COLOR_BG);
        setContentView(root);
        loadProfiles();
        int previous = prefs.getInt("active", -1);
        if (previous >= 0 && previous < profiles.size()) {
            selectedIndex = previous;
            showWeb(profiles.get(previous).url);
        } else showSettings();
    }

    private void loadProfiles() {
        profiles.clear();
        try {
            JSONArray values = new JSONArray(prefs.getString("profiles", "[]"));
            for (int i=0; i<Math.min(12, values.length()); i++) {
                JSONObject o=values.getJSONObject(i);
                String url=ServerAddress.normalize(o.getString("url"));
                profiles.add(new ServerProfile(o.optString("name", "Сервер"), url));
            }
        } catch (Exception ignored) {
            // Corrupt local preferences do not prevent opening the settings page.
        }
    }

    private void storeProfiles() {
        JSONArray json=new JSONArray();
        try {
            for (ServerProfile p:profiles) {
                JSONObject obj=new JSONObject();
                obj.put("name", p.name);obj.put("url", p.url);
                json.put(obj);
            }
        } catch (Exception ignored) {}
        prefs.edit().putString("profiles",json.toString()).putInt("active",selectedIndex).apply();
    }

    private int dp(float px) {return Math.round(px * getResources().getDisplayMetrics().density);}

    private GradientDrawable background(int color, int stroke) {
        GradientDrawable d=new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(dp(12));
        if (stroke!=0) d.setStroke(dp(1),stroke);
        return d;
    }

    private LinearLayout vertical() {
        LinearLayout v=new LinearLayout(this);
        v.setOrientation(LinearLayout.VERTICAL);
        return v;
    }

    private TextView text(String value, int sp, int color) {
        TextView view=new TextView(this);
        view.setText(value);view.setTextSize(sp);view.setTextColor(color);
        view.setGravity(Gravity.CENTER_VERTICAL);
        return view;
    }

    private void addBlock(LinearLayout to, View element, int marginTop) {
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.topMargin=dp(marginTop);
        to.addView(element,p);
    }

    private EditText entry(String hint, String value, int inputType) {
        EditText e=new EditText(this);
        e.setSingleLine(true);
        e.setTextSize(17);
        e.setPadding(dp(15),dp(12),dp(15),dp(12));
        e.setTextColor(COLOR_TEXT);
        e.setHintTextColor(COLOR_MUTED);
        e.setHint(hint);
        e.setText(value);
        e.setInputType(inputType);
        e.setSelectAllOnFocus(false);
        e.setBackground(background(COLOR_BG,0xFF395470));
        e.setImeOptions(EditorInfo.IME_ACTION_DONE);
        return e;
    }

    private Button button(String title, boolean primary) {
        Button b=new Button(this);
        b.setText(title);b.setAllCaps(false);b.setTextSize(16);
        b.setTextColor(COLOR_TEXT);
        b.setMinHeight(dp(isTv ? 58:50));
        b.setBackground(background(primary?COLOR_ACCENT:0xFF263E57,0));
        b.setFocusable(true);
        return b;
    }

    private void showSettings() {
        showingSetup=true;
        root.removeAllViews();
        ScrollView scroll=new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);
        LinearLayout panel=vertical();
        panel.setPadding(dp(22),dp(20),dp(22),dp(26));
        panel.setBackground(background(COLOR_PANEL,0xFF2B4A63));
        TextView heading=text("TORRENT HUB",26,COLOR_TEXT);
        heading.setTypeface(Typeface.DEFAULT,Typeface.BOLD);
        addBlock(panel,heading,0);
        addBlock(panel,text("Сервер Home Assistant • Android / Android TV",14,COLOR_MUTED),5);
        addBlock(panel,text("Сохранённые серверы",14,COLOR_MUTED),25);

        List<String> names=new ArrayList<>();
        for(ServerProfile profile:profiles)names.add(profile.name+" — "+profile.url);
        names.add("＋ Добавить другой сервер");
        chooser=new Spinner(this);
        ArrayAdapter<String> adapter=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,names);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        chooser.setAdapter(adapter);
        chooser.setBackground(background(COLOR_BG,0xFF395470));
        chooser.setMinimumHeight(dp(isTv?60:52));
        addBlock(panel,chooser,7);

        addBlock(panel,text("Название подключения",14,COLOR_MUTED),20);
        nameField=entry("Например: Дом, Tailscale, Keenetic","Дом",1);
        addBlock(panel,nameField,7);
        addBlock(panel,text("Адрес или URL сервера",14,COLOR_MUTED),17);
        addressField=entry("IP:порт или https://домен",DEFAULT_ADDRESS,17);
        addBlock(panel,addressField,7);

        TextView examples=text("Примеры:\n192.168.1.100:8766\n100.101.102.103:8766\n192-168-1-100-via1\nhttps://torr.vapity.keenetic.link",13,COLOR_MUTED);
        examples.setLineSpacing(dp(2),1);
        addBlock(panel,examples,12);

        Button connect=button("Подключиться",true);
        addBlock(panel,connect,24);
        connect.setOnClickListener(v->connectFromSettings());

        Button delete=button("Удалить выбранный сервер",false);
        addBlock(panel,delete,8);
        delete.setOnClickListener(v->{
            int idx=chooser.getSelectedItemPosition();
            if(idx>=0 && idx<profiles.size()){
                profiles.remove(idx);
                selectedIndex=-1;
                storeProfiles();
                showSettings();
            } else toast("Выбери сохранённый сервер.");
        });

        TextView help=text("При смене IP, Tailscale-адреса или порта здесь можно в любой момент выбрать другой сервер. Никакой регистрации не требуется.",13,COLOR_MUTED);
        help.setLineSpacing(dp(3),1f);
        addBlock(panel,help,24);

        chooser.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(android.widget.AdapterView<?> parent, View view, int position, long id) {
                if(position>=0 && position<profiles.size()){
                    ServerProfile selected=profiles.get(position);
                    nameField.setText(selected.name);
                    addressField.setText(selected.url);
                } else {
                    nameField.setText("Новый сервер");
                    addressField.setText(DEFAULT_ADDRESS);
                }
            }
            @Override public void onNothingSelected(android.widget.AdapterView<?> parent) {}
        });
        int chosen=selectedIndex>=0&&selectedIndex<profiles.size()?selectedIndex:profiles.size();
        chooser.setSelection(chosen);

        scroll.addView(panel);
        FrameLayout.LayoutParams scrollP=new FrameLayout.LayoutParams(
                Math.min(getResources().getDisplayMetrics().widthPixels-dp(30),dp(isTv?630:520)),
                ViewGroup.LayoutParams.MATCH_PARENT,Gravity.CENTER);
        scrollP.topMargin=dp(16);scrollP.bottomMargin=dp(16);
        root.addView(scroll,scrollP);
        addressField.setOnEditorActionListener((v,id,event)->{
            connectFromSettings();return true;
        });
        connect.requestFocus();
    }

    private void connectFromSettings() {
        String url;
        try {url=ServerAddress.normalize(addressField.getText().toString());}
        catch (IllegalArgumentException e) {addressField.setError(e.getMessage());return;}
        String label=nameField.getText().toString().trim();
        if(label.isEmpty())label="Torrent Hub";
        if(profiles.size()>=12 && chooser.getSelectedItemPosition()>=profiles.size()){
            toast("Можно сохранить до 12 серверов.");return;
        }
        int current=chooser.getSelectedItemPosition();
        if(current>=0&&current<profiles.size()){
            profiles.set(current,new ServerProfile(label,url));
            selectedIndex=current;
        } else {
            profiles.add(new ServerProfile(label,url));
            selectedIndex=profiles.size()-1;
        }
        storeProfiles();
        showWeb(url);
    }

    private void showWeb(String url) {
        currentUrl=url;
        showingSetup=false;
        root.removeAllViews();

        LinearLayout page=vertical();
        page.setBackgroundColor(COLOR_BG);
        LinearLayout top=new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.setPadding(dp(7),dp(4),dp(7),dp(4));
        top.setBackgroundColor(COLOR_BG);

        Button settings=button("⚙ Сервер",false);
        settings.setTextSize(13);
        settings.setMinHeight(dp(42));
        top.addView(settings,new LinearLayout.LayoutParams(0,dp(44),1f));
        settings.setOnClickListener(v->showSettings());

        TextView name=text(selectedIndex>=0&&selectedIndex<profiles.size()?
            profiles.get(selectedIndex).name:"Torrent Hub",14,COLOR_MUTED);
        name.setGravity(Gravity.CENTER);
        top.addView(name,new LinearLayout.LayoutParams(0,dp(44),2f));

        Button refresh=button("↻ Обновить",false);
        refresh.setMinHeight(dp(42));refresh.setTextSize(13);
        top.addView(refresh,new LinearLayout.LayoutParams(0,dp(44),1f));
        refresh.setOnClickListener(v->{
            if(web!=null)web.reload();
        });
        page.addView(top);

        progress=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setVisibility(View.GONE);
        page.addView(progress,new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,dp(3)));

        try {
            if(web==null)makeWebView();
        } catch(Throwable problem) {
            showError("Не удалось запустить Android WebView.\nПроверь, включён ли Android System WebView или Chrome на устройстве.");
            return;
        }
        if(web.getParent()!=null)((ViewGroup)web.getParent()).removeView(web);
        page.addView(web,new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,0,1f));
        root.addView(page,new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.MATCH_PARENT));
        web.requestFocus();
        if (!url.equals(web.getUrl())) web.loadUrl(url);
    }

    @android.annotation.SuppressLint("SetJavaScriptEnabled")
    private void makeWebView() {
        web=new WebView(this);
        web.setBackgroundColor(COLOR_BG);
        web.setFocusable(true);web.setFocusableInTouchMode(true);
        web.setVerticalScrollBarEnabled(true);
        web.setHorizontalScrollBarEnabled(false);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        WebSettings w=web.getSettings();
        w.setJavaScriptEnabled(true);
        w.setDomStorageEnabled(true);
        w.setDatabaseEnabled(true);
        w.setDefaultTextEncodingName("UTF-8");
        w.setBuiltInZoomControls(false);
        w.setDisplayZoomControls(false);
        w.setSupportMultipleWindows(false);
        w.setJavaScriptCanOpenWindowsAutomatically(false);
        w.setAllowFileAccess(false);
        w.setAllowContentAccess(false);
        w.setAllowFileAccessFromFileURLs(false);
        w.setAllowUniversalAccessFromFileURLs(false);
        w.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        w.setCacheMode(WebSettings.LOAD_DEFAULT);
        w.setMediaPlaybackRequiresUserGesture(true);
        if(Build.VERSION.SDK_INT>=26) w.setSafeBrowsingEnabled(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);

        web.setWebViewClient(new WebViewClient(){
            @Override public void onPageStarted(WebView view,String url,android.graphics.Bitmap favicon){
                if(progress!=null){progress.setProgress(8);progress.setVisibility(View.VISIBLE);}
            }
            @Override public void onPageFinished(WebView view,String url){
                if(progress!=null)progress.setVisibility(View.GONE);
                if(isTv && onOurServer(url)) {
                    view.evaluateJavascript(
                        "(function(){var id='torrenthub-tv-focus';if(document.getElementById(id))return;" +
                        "var s=document.createElement('style');s.id=id;" +
                        "s.textContent='button:focus,input:focus,select:focus,a:focus{outline:3px solid #19d8cf!important;outline-offset:3px!important}';" +
                        "document.head.appendChild(s);})()",null);
                }
            }
            @Override public void onReceivedError(WebView view,WebResourceRequest request,WebResourceError error){
                if(request.isForMainFrame()) showError("Не удалось подключиться к "+currentUrl+
                        "\n"+error.getDescription()+
                        "\n\nПроверь подключение к сети, Home Assistant и Tailscale.");
            }
            @Override public void onReceivedHttpError(WebView view,WebResourceRequest request,WebResourceResponse response){
                if(request.isForMainFrame() && response.getStatusCode()>=500){
                    showError("Сервер "+currentUrl+" отвечает ошибкой HTTP "+response.getStatusCode()+
                            ".\nПроверь работу Torrent Hub в Home Assistant.");
                }
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
                if(!request.isForMainFrame())return false;
                Uri link=request.getUrl();
                String scheme=link.getScheme();
                if("http".equalsIgnoreCase(scheme)||"https".equalsIgnoreCase(scheme)){
                    if(onOurServer(link.toString()))return false;
                    try {startActivity(new Intent(Intent.ACTION_VIEW,link));}
                    catch(Exception e){toast("Для внешней ссылки нужен браузер");}
                    return true;
                }
                if("magnet".equalsIgnoreCase(scheme)){
                    try {startActivity(new Intent(Intent.ACTION_VIEW,link));}
                    catch(Exception e){toast("Нет приложения для magnet. Используй кнопку копирования.");}
                    return true;
                }
                return true; // deny javascript:, file:, content: and unknown schemes
            }
            @Override public void onReceivedSslError(WebView view,android.webkit.SslErrorHandler handler,
                                                       android.net.http.SslError error){
                handler.cancel();
                showError("HTTPS-сертификат сервера не прошёл проверку. Проверь домен Keenetic и сертификат. Небезопасный обход отключён.");
            }
        });
        web.setWebChromeClient(new WebChromeClient(){
            @Override public void onProgressChanged(WebView view,int value){
                if(progress!=null){progress.setProgress(value);progress.setVisibility(value<100?View.VISIBLE:View.GONE);}
            }
        });
        web.setDownloadListener(new DownloadListener(){
            @Override public void onDownloadStart(String url,String agent,String disposition,
                                                  String mime,long length){
                startDownload(url,agent,disposition,mime);
            }
        });
    }

    private boolean onOurServer(String url) {
        try {
            URI a=new URI(currentUrl), b=new URI(url);
            int ap=a.getPort()<0?("https".equals(a.getScheme())?443:80):a.getPort();
            int bp=b.getPort()<0?("https".equals(b.getScheme())?443:80):b.getPort();
            return a.getScheme().equalsIgnoreCase(b.getScheme())
                    && a.getHost()!=null && a.getHost().equalsIgnoreCase(b.getHost()) && ap==bp;
        }catch(URISyntaxException e){return false;}
    }

    private void showError(String message) {
        if(showingSetup)return;
        root.removeAllViews();
        LinearLayout box=vertical();box.setPadding(dp(22),dp(24),dp(22),dp(20));
        TextView title=text("Не удалось открыть Torrent Hub",21,COLOR_TEXT);
        title.setTypeface(Typeface.DEFAULT,Typeface.BOLD);
        addBlock(box,title,0);
        TextView description=text(message,15,COLOR_MUTED);
        description.setLineSpacing(dp(3),1);
        addBlock(box,description,18);
        Button retry=button("Повторить подключение",true);
        addBlock(box,retry,25);
        retry.setOnClickListener(v->{if(web!=null)web.stopLoading();showWeb(currentUrl);if(web!=null)web.reload();});
        Button other=button("Изменить адрес сервера",false);
        addBlock(box,other,10);
        other.setOnClickListener(v->showSettings());
        FrameLayout.LayoutParams p=new FrameLayout.LayoutParams(
                Math.min(getResources().getDisplayMetrics().widthPixels-dp(32),dp(540)),
                ViewGroup.LayoutParams.WRAP_CONTENT,Gravity.CENTER);
        root.addView(box,p);
        retry.requestFocus();
    }

    private void startDownload(String url,String agent,String disposition,String mime){
        if(!"http".equalsIgnoreCase(Uri.parse(url).getScheme()) &&
           !"https".equalsIgnoreCase(Uri.parse(url).getScheme())){
            toast("Поддерживается скачивание только по HTTP/HTTPS");return;
        }
        pendingDownloadUrl=url;pendingDownloadAgent=agent;
        pendingDownloadDisposition=disposition;pendingDownloadType=mime;
        if(Build.VERSION.SDK_INT<=28 &&
                checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)!=PackageManager.PERMISSION_GRANTED){
            requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE},REQUEST_DOWNLOAD);
            return;
        }
        enqueueDownload();
    }

    @Override public void onRequestPermissionsResult(int requestCode,String[] permissions,int[] results){
        super.onRequestPermissionsResult(requestCode,permissions,results);
        if(requestCode==REQUEST_DOWNLOAD){
            if(results.length>0 && results[0]==PackageManager.PERMISSION_GRANTED)enqueueDownload();
            else toast("Без доступа к загрузкам файл не сохранён");
        }
    }

    private void enqueueDownload(){
        if(pendingDownloadUrl==null)return;
        String url=pendingDownloadUrl;
        String name=URLUtil.guessFileName(url,pendingDownloadDisposition,pendingDownloadType);
        name=name.replaceAll("[^a-zA-Z0-9а-яА-ЯёЁ._ -]","_");
        if(name.isEmpty())name="torrent";
        if(!name.toLowerCase(Locale.ROOT).endsWith(".torrent") &&
           (pendingDownloadType!=null && pendingDownloadType.contains("bittorrent")))name+=".torrent";
        try {
            DownloadManager.Request request=new DownloadManager.Request(Uri.parse(url));
            request.setTitle(name);
            request.setDescription("Torrent Hub");
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            if(pendingDownloadAgent!=null)request.addRequestHeader("User-Agent",pendingDownloadAgent);
            String cookies=CookieManager.getInstance().getCookie(url);
            if(cookies!=null&&!cookies.isEmpty())request.addRequestHeader("Cookie",cookies);
            String unique=System.currentTimeMillis()+"-"+name;
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS,unique);
            DownloadManager manager=(DownloadManager)getSystemService(DOWNLOAD_SERVICE);
            manager.enqueue(request);
            toast("Загрузка отправлена в папку Downloads");
        }catch(Exception ex){
            toast("Не удалось скачать файл: "+ex.getMessage());
        }finally{pendingDownloadUrl=null;}
    }

    private void toast(String text){Toast.makeText(this,text,Toast.LENGTH_LONG).show();}

    @Override public boolean onKeyDown(int keyCode,KeyEvent event){
        if(keyCode==KeyEvent.KEYCODE_MENU || keyCode==KeyEvent.KEYCODE_SETTINGS){
            showSettings();return true;
        }
        if(keyCode==KeyEvent.KEYCODE_BACK && !showingSetup){
            if(web!=null && web.getParent()!=null && web.canGoBack()){web.goBack();return true;}
            showSettings();return true;
        }
        return super.onKeyDown(keyCode,event);
    }

    @Override protected void onPause(){if(web!=null)web.onPause();super.onPause();}
    @Override protected void onResume(){super.onResume();if(web!=null)web.onResume();}
    @Override protected void onDestroy(){
        if(web!=null){if(web.getParent()!=null)((ViewGroup)web.getParent()).removeView(web);web.destroy();web=null;}
        super.onDestroy();
    }
}
