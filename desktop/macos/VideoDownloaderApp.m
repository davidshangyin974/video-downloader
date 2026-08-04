#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#import <signal.h>

@interface AppDelegate : NSObject <NSApplicationDelegate, NSWindowDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, strong) NSTask *backendProcess;
@property(nonatomic, strong) NSFileHandle *logHandle;
@property(nonatomic, copy) NSString *port;
@end

@implementation AppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    [NSApp activateIgnoringOtherApps:YES];
    NSString *configuredPort = NSProcessInfo.processInfo.environment[@"VIDEO_DOWNLOADER_PORT"];
    self.port = configuredPort.length > 0 ? configuredPort : @"8000";
    [self createWindow];

    NSError *error = nil;
    NSURL *projectRoot = [self loadProjectRoot:&error];
    if (projectRoot == nil || ![self launchBackend:projectRoot error:&error]) {
        [self showStartupError:error.localizedDescription ?: @"未知启动错误。"];
        return;
    }
    [self waitForBackend];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    return YES;
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    [self stopBackend];
}

- (void)windowWillClose:(NSNotification *)notification {
    [self stopBackend];
}

- (void)createWindow {
    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = WKWebsiteDataStore.defaultDataStore;
    self.webView = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:configuration];

    NSRect frame = NSMakeRect(0, 0, 1280, 820);
    self.window = [[NSWindow alloc]
        initWithContentRect:frame
                  styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                            NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
                    backing:NSBackingStoreBuffered
                      defer:NO];
    self.window.title = @"Video Downloader";
    self.window.minSize = NSMakeSize(920, 620);
    self.window.contentView = self.webView;
    self.window.delegate = self;
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
}

- (NSURL *)loadProjectRoot:(NSError **)error {
    NSURL *resourceURL = NSBundle.mainBundle.resourceURL;
    if (resourceURL == nil) {
        if (error) *error = [self launcherError:@"应用资源目录不存在。请重新构建桌面应用。"];
        return nil;
    }
    NSURL *rootFile = [resourceURL URLByAppendingPathComponent:@"project-root.txt"];
    NSString *rootValue = [NSString stringWithContentsOfURL:rootFile encoding:NSUTF8StringEncoding error:error];
    rootValue = [rootValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (rootValue.length == 0) {
        if (error) *error = [self launcherError:@"桌面应用没有记录项目位置。请重新构建桌面应用。"];
        return nil;
    }

    NSURL *root = [NSURL fileURLWithPath:rootValue isDirectory:YES];
    NSArray<NSString *> *required = @[
        @"backend/.venv/bin/python",
        @"backend/run.py",
        @"frontend/dist/index.html",
    ];
    for (NSString *relativePath in required) {
        NSURL *path = [root URLByAppendingPathComponent:relativePath];
        if (![NSFileManager.defaultManager fileExistsAtPath:path.path]) {
            if (error) *error = [self launcherError:[NSString stringWithFormat:@"缺少运行文件：%@\n请在项目目录执行 make macos-app。", path.path]];
            return nil;
        }
    }
    return root;
}

- (BOOL)launchBackend:(NSURL *)projectRoot error:(NSError **)error {
    NSURL *logDirectory = [NSFileManager.defaultManager.homeDirectoryForCurrentUser
        URLByAppendingPathComponent:@"Library/Logs/Video Downloader"
                         isDirectory:YES];
    if (![NSFileManager.defaultManager createDirectoryAtURL:logDirectory
                                withIntermediateDirectories:YES
                                                 attributes:nil
                                                      error:error]) {
        return NO;
    }
    NSURL *logURL = [logDirectory URLByAppendingPathComponent:@"backend.log"];
    if (![NSFileManager.defaultManager fileExistsAtPath:logURL.path]) {
        [NSFileManager.defaultManager createFileAtPath:logURL.path contents:nil attributes:nil];
    }
    NSFileHandle *handle = [NSFileHandle fileHandleForWritingToURL:logURL error:error];
    if (handle == nil) return NO;
    [handle seekToEndOfFile];

    NSTask *process = [[NSTask alloc] init];
    process.executableURL = [projectRoot URLByAppendingPathComponent:@"backend/.venv/bin/python"];
    process.arguments = @[[projectRoot URLByAppendingPathComponent:@"backend/run.py"].path];
    process.currentDirectoryURL = projectRoot;
    NSMutableDictionary<NSString *, NSString *> *environment = [NSProcessInfo.processInfo.environment mutableCopy];
    environment[@"VIDEO_DOWNLOADER_DESKTOP"] = @"1";
    environment[@"VIDEO_DOWNLOADER_PORT"] = self.port;
    process.environment = environment;
    process.standardOutput = handle;
    process.standardError = handle;
    if (![process launchAndReturnError:error]) {
        [handle closeFile];
        return NO;
    }

    self.backendProcess = process;
    self.logHandle = handle;
    return YES;
}

- (void)waitForBackend {
    __weak typeof(self) weakSelf = self;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        BOOL ready = NO;
        NSURL *healthURL = [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%@/health", self.port]];
        for (NSInteger attempt = 0; attempt < 80; attempt++) {
            NSData *data = [NSData dataWithContentsOfURL:healthURL];
            if (data != nil) {
                NSDictionary *value = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
                if ([value[@"status"] isEqual:@"ok"]) {
                    ready = YES;
                    break;
                }
            }
            usleep(250000);
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            AppDelegate *self = weakSelf;
            if (self == nil) return;
            if (ready) {
                NSURL *appURL = [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%@", self.port]];
                NSURLRequest *request = [NSURLRequest requestWithURL:appURL];
                [self.webView loadRequest:request];
            } else {
                [self showStartupError:@"本地下载服务未能在 20 秒内启动。\n请查看 ~/Library/Logs/Video Downloader/backend.log"];
            }
        });
    });
}

- (void)showStartupError:(NSString *)message {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.alertStyle = NSAlertStyleCritical;
    alert.messageText = @"Video Downloader 启动失败";
    alert.informativeText = message;
    [alert addButtonWithTitle:@"退出"];
    [alert runModal];
    [NSApp terminate:nil];
}

- (void)stopBackend {
    NSTask *process = self.backendProcess;
    self.backendProcess = nil;
    if (process != nil && process.isRunning) {
        [process terminate];
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:3];
        while (process.isRunning && deadline.timeIntervalSinceNow > 0) {
            usleep(50000);
        }
        if (process.isRunning) {
            kill(process.processIdentifier, SIGKILL);
        }
    }
    [self.logHandle closeFile];
    self.logHandle = nil;
}

- (NSError *)launcherError:(NSString *)message {
    return [NSError errorWithDomain:@"local.wsy.video-downloader"
                               code:1
                           userInfo:@{NSLocalizedDescriptionKey: message}];
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        AppDelegate *delegate = [[AppDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
