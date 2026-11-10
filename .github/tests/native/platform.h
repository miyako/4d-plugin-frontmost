#pragma once

#include <Carbon/Carbon.h>
#include <Cocoa/Cocoa.h>

// Replace the application's platform calls before compiling its real dispatcher.
@interface OwnershipTestApplication : NSObject
+ (id)currentApplication;
- (BOOL)isActive;
- (BOOL)activateWithOptions:(NSApplicationActivationOptions)options;
@end

#define NSRunningApplication OwnershipTestApplication
