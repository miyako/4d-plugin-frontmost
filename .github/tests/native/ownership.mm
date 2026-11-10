#include "4DPluginAPI.h"
#include "PrivateTypes.h"
#include "EntryPoints.h"

#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>

static bool active = false;
static unsigned activationCalls = 0;

@implementation OwnershipTestApplication
+ (id)currentApplication
{
    static OwnershipTestApplication *application = [OwnershipTestApplication new];
    return application;
}
- (BOOL)isActive { return active; }
- (BOOL)activateWithOptions:(NSApplicationActivationOptions)options
{
    assert(options == NSApplicationActivateIgnoringOtherApps);
    ++activationCalls;
    return YES;
}
@end

static std::set<PA_Unichar *> strings;
static std::map<PA_Handle, size_t> handles;
struct TestPicture { unsigned refs; unsigned serial; };
static std::set<TestPicture *> pictures;
static unsigned serial = 0;
static bool nullEmpty = false;
static bool failHandle = false;
static bool failCommand = false;
static CUTF16String expectedText;
static CUTF16String lastType;
static unsigned commandCalls = 0;
static bool checkPathInitialOwner = false;

static void require(bool condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "ownership assertion: %s\n", message);
        abort();
    }
}

static CUTF16String text(const PA_Unistring &value)
{
    PA_Unichar *buffer = value.fString;
    if (!buffer) {
        require(value.fLength == 0, "null string length");
        return CUTF16String();
    }
    require(strings.count(buffer), "read of non-live string");
    require(buffer[value.fLength] == 0, "missing UTF16 terminator");
    return CUTF16String(buffer, value.fLength);
}

static PA_Unistring makeString(const PA_Unichar *source)
{
    size_t units = 0;
    if (source)
        while (source[units]) ++units;
    PA_Unistring result = {};
    if (!units && nullEmpty) return result;
    result.fString = new PA_Unichar[units + 1];
    if (units) memcpy(result.fString, source, units * sizeof(PA_Unichar));
    result.fString[units] = 0;
    result.fLength = static_cast<PA_long32>(units);
    PA_Unichar *buffer = result.fString;
    strings.insert(buffer);
    return result;
}

static void disposeString(PA_Unistring value)
{
    PA_Unichar *buffer = value.fString;
    if (!buffer) return;
    require(strings.erase(buffer) == 1, "double string disposal");
    delete[] buffer;
}

static PA_Picture makePicture(bool empty)
{
    if (empty && nullEmpty) return nullptr;
    TestPicture *p = new TestPicture{1, ++serial};
    pictures.insert(p);
    return p;
}

static TestPicture *livePicture(PA_Picture value)
{
    TestPicture *p = static_cast<TestPicture *>(value);
    require(p && pictures.count(p), "picture must survive its handoff");
    return p;
}

static void disposePicture(PA_Picture value)
{
    if (!value) return;
    TestPicture *p = livePicture(value);
    if (--p->refs == 0) {
        pictures.erase(p);
        delete p;
    }
}

static void clear(PA_Variable *value)
{
    switch (value->fType) {
        case eVK_Unistring: disposeString(value->uValue.fString); break;
        case eVK_Picture: disposePicture(value->uValue.fPicture); break;
        case eVK_ArrayUnicode:
            if (value->uValue.fArray.fData) {
                PA_Unistring *items = reinterpret_cast<PA_Unistring *>(*value->uValue.fArray.fData);
                for (PA_long32 i = 0; i <= value->uValue.fArray.fNbElements; ++i)
                    disposeString(items[i]);
                PA_DisposeHandle(value->uValue.fArray.fData);
            }
            break;
        case eVK_Undefined:
        case eVK_Longint: break;
        default: require(false, "invalid sentinel or unsupported variable cleanup");
    }
    *value = {};
    value->fType = eVK_Undefined;
}

static void replacePicture(PA_Variable *arg)
{
    clear(arg);
    PA_SetPictureVariable(arg, makePicture(false));
}

static void engine(short selector, EngineBlock *eb)
{
    switch (selector) {
        case EX_CREATE_UNISTRING:
            eb->fUniString1 = makeString(reinterpret_cast<PA_Unichar *>(eb->fHandle));
            break;
        case EX_DISPOSE_UNISTRING:
            disposeString(eb->fUniString1);
            break;
        case EX_SET_UNISTRING: {
            PA_Unistring copy = makeString(reinterpret_cast<PA_Unichar *>(eb->fHandle));
            disposeString(eb->fUniString1);
            eb->fUniString1 = copy;
            break;
        }
        case EX_CREATE_PICTURE:
            eb->fPicture = makePicture(eb->fParam1 == 0);
            eb->fError = 0;
            break;
        case EX_DUPLICATE_PICTURE:
            if (eb->fPicture) {
                TestPicture *p = livePicture(eb->fPicture);
                if (eb->fParam1 == 0) ++p->refs;
                else eb->fPicture = makePicture(false);
            }
            break;
        case EX_DISPOSE_PICTURE:
            disposePicture(eb->fPicture);
            break;
        case EX_GET_PICTURE_DATA:
            // Metadata output ownership is deliberately not assumed by this audit.
            eb->fError = eER_IncompatibleTypes;
            eb->fUniString1 = {};
            break;
        case EX_CLEAR_VARIABLE:
            clear(static_cast<PA_Variable *>(eb->fPtr1));
            break;
        case EX_HANDLE_MANAGER: {
            PA_Handle h = eb->fHandle;
            eb->fError = 0;
            switch (eb->fParam1) {
                case 1:
                    if (failHandle) {
                        eb->fHandle = nullptr;
                        eb->fError = eER_IncompatibleTypes;
                        break;
                    }
                    h = new char *;
                    *h = static_cast<char *>(calloc(1, eb->fParam2 ? eb->fParam2 : 1));
                    require(*h, "handle allocation");
                    handles[h] = eb->fParam2;
                    eb->fHandle = h;
                    break;
                case 2:
                    require(handles.erase(h) == 1, "handle disposal");
                    free(*h);
                    delete h;
                    break;
                case 3: {
                    require(handles.count(h), "resize live handle");
                    size_t old = handles.at(h);
                    char *data = static_cast<char *>(realloc(*h, eb->fParam2 ? eb->fParam2 : 1));
                    require(data, "handle resize");
                    *h = data;
                    if (static_cast<size_t>(eb->fParam2) > old)
                        memset(data + old, 0, eb->fParam2 - old);
                    handles[h] = eb->fParam2;
                    break;
                }
                case 4: eb->fParam2 = handles.at(h); break;
                case 5: eb->fParam3 = reinterpret_cast<sLONG_PTR>(*h); break;
                case 6: require(handles.count(h), "unlock live handle"); break;
                default: require(false, "unexpected handle operation");
            }
            break;
        }
        case EX_CONVERT_CHARSET_TO_CHARSET: {
            require(eb->fParam2 == eVTC_UTF_8 && eb->fParam4 == eVTC_UTF_16,
                    "expected UTF8 to UTF16 conversion");
            CFStringRef s = CFStringCreateWithBytes(kCFAllocatorDefault,
                static_cast<UInt8 *>(eb->fPtr1), eb->fParam1, kCFStringEncodingUTF8, false);
            require(s, "valid synthetic UTF8");
            size_t units = CFStringGetLength(s);
            size_t bytes = units * sizeof(PA_Unichar);
            require(bytes <= static_cast<size_t>(eb->fParam3), "conversion capacity is bytes");
            CFStringGetCharacters(s, CFRangeMake(0, units), static_cast<UniChar *>(eb->fPtr2));
            if (bytes + sizeof(PA_Unichar) <= static_cast<size_t>(eb->fParam3))
                static_cast<PA_Unichar *>(eb->fPtr2)[units] = 0;
            eb->fParam3 = bytes; // excludes terminator, never UTF16 units
            eb->fError = 0;
            CFRelease(s);
            break;
        }
        case EX_EXECUTE_COMMAND_BY_ID: {
            ++commandCalls;
            PA_Variable **args = static_cast<PA_Variable **>(eb->fPtr1);
            eb->fError = 0;
            if (failCommand) {
                eb->fError = eER_IncompatibleTypes;
                break;
            }
            switch (eb->fParam1) {
                case 678:
                    if (checkPathInitialOwner)
                        require(strings.size() == 1, "overwritten initial path string owner");
                    require(args[0]->fType == eVK_Unistring, "path argument kind");
                    require(text(args[0]->uValue.fString) == expectedText, "exact path text");
                    replacePicture(args[1]);
                    break;
                case 1002:
                    require(args[1]->fType == eVK_Unistring, "type argument kind");
                    lastType = text(args[1]->uValue.fString);
                    require(lastType == expectedText, "exact converted type");
                    replacePicture(args[0]);
                    break;
                case 988:
                    require(PA_GetLongintVariable(*args[1]) == 101, "grayscale option");
                    replacePicture(args[0]);
                    break;
                case 457:
                    if (args[0]->uValue.fPicture) livePicture(args[0]->uValue.fPicture);
                    PA_SetLongintVariable(args[1], 31);
                    PA_SetLongintVariable(args[2], 17);
                    break;
                case 679:
                    if (args[0]->uValue.fPicture) livePicture(args[0]->uValue.fPicture);
                    require(PA_GetLongintVariable(*args[2]) == 8, "thumbnail width");
                    require(PA_GetLongintVariable(*args[3]) == 9, "thumbnail height");
                    replacePicture(args[1]);
                    break;
                default: require(false, "unexpected command");
            }
            break;
        }
        case EX_CALL_BY_PROCID: {
            PA_Variable *args = reinterpret_cast<PA_Variable *>(*eb->fHandle);
            PA_Unistring result = makeString(expectedText.c_str());
            PA_SetStringVariable(&args[0], &result);
            eb->fError = 0;
            break;
        }
        default:
            fprintf(stderr, "unexpected engine selector: %d\n", selector);
            abort();
    }
}

static void balanced()
{
    require(strings.empty(), "string balance");
    require(pictures.empty(), "picture balance");
    require(handles.empty(), "handle balance");
}

static CUTF16String utf16(const char *source)
{
    CFStringRef s = CFStringCreateWithCString(kCFAllocatorDefault, source, kCFStringEncodingUTF8);
    require(s, "fixture UTF8");
    CUTF16String result(CFStringGetLength(s), 0);
    if (!result.empty())
        CFStringGetCharacters(s, CFRangeMake(0, result.size()), &result[0]);
    CFRelease(s);
    return result;
}

static void pathCase()
{
    for (const char *s : {"synthetic.png", "\xE6\x97\xA5.png", "\xF0\x9F\x98\x80.png", ""}) {
        CUTF8String path(reinterpret_cast<const uint8_t *>(s));
        expectedText = utf16(s);
        {
            CPicture picture;
            picture.fromFileAtPath(&path);
            livePicture(picture.getPicture());
        }
        balanced();
    }
}

static void pathOwnerCase(bool initial)
{
    CUTF8String path(reinterpret_cast<const uint8_t *>(initial ? "synthetic.png" : ""));
    expectedText = utf16(initial ? "synthetic.png" : "");
    checkPathInitialOwner = initial;
    {
        CPicture p;
        p.fromFileAtPath(&path);
        livePicture(p.getPicture());
    }
    balanced();
}

static void bytesCase()
{
    const uint8_t data[] = {1, 2, 3};
    {
        CPicture p;
        p.setBytes(data, sizeof(data));
        livePicture(p.getPicture());
        p.setBytes(data, sizeof(data));
        livePicture(p.getPicture());
    }
    balanced();
}

static void helpersCase(const std::string &name)
{
    {
        CPicture p;
        if (name == "gray") {
            PA_Picture result = p.createGrayScale();
            livePicture(result);
            PA_DisposePicture(result);
        } else if (name == "size") {
            unsigned width, height;
            p.getSize(&width, &height);
            require(width == 31 && height == 17, "exact dimensions");
        } else if (name == "thumbnail") {
            PA_Picture result = p.createThumbnail(8, 9);
            livePicture(result);
            PA_DisposePicture(result);
        } else {
            std::vector<const char *> types = name == "copy-empty"
                ? std::vector<const char *>{""}
                : std::vector<const char *>{".png", "\xE6\x97\xA5", "\xF0\x9F\x98\x80", ""};
            for (const char *s : types) {
                expectedText = utf16(s);
                PA_Picture result = p.createCopy(s);
                livePicture(result);
                PA_DisposePicture(result);
            }
        }
    }
    balanced();
}

static void handoffsCase()
{
    PA_Picture returned = nullptr;
    PA_Picture gray = nullptr, thumbnail = nullptr, copy = nullptr;
    PA_Picture param = PA_CreatePicture(nullptr, 1);
    uint8_t *params[] = {reinterpret_cast<uint8_t *>(&param)};
    {
        CPicture p;
        p.fromParamAtIndex(params, 1);
        require(livePicture(param)->refs == 2, "borrowed parameter retained");
        unsigned width, height;
        p.getSize(&width, &height);
        livePicture(param);
        thumbnail = p.createThumbnail(8, 9);
        gray = p.createGrayScale();
        expectedText = utf16("");
        copy = p.createCopy("");
        livePicture(param);
        for (PA_Picture result : {gray, thumbnail, copy}) {
            livePicture(result);
            PA_DisposePicture(result);
        }
        p.toParamAtIndex(params, 1);
        p.setReturn(reinterpret_cast<sLONG_PTR *>(&returned));
    }
    livePicture(returned);
    livePicture(param);
    PA_DisposePicture(returned);
    PA_DisposePicture(param);
    balanced();
}

static void charsetCase()
{
    struct Fixture { const char *utf8; CUTF16String utf16; };
    const Fixture fixtures[] = {
        {"A", CUTF16String{0x41}},
        {"\xE6\x97\xA5", CUTF16String{0x65e5}},
        {"\xF0\x9F\x98\x80", CUTF16String{0xd83d, 0xde00}},
        {"", CUTF16String{}},
    };
    for (const Fixture &f : fixtures) {
        require(utf16(f.utf8) == f.utf16, "literal fixture code units");
        size_t bytes = f.utf16.size() * sizeof(PA_Unichar);
        for (size_t capacity : {bytes, bytes + sizeof(PA_Unichar)}) {
            std::vector<PA_Unichar> buffer(capacity / sizeof(PA_Unichar) + 1, 0xabab);
            PA_long32 length = PA_ConvertCharsetToCharset(
                const_cast<char *>(f.utf8), strlen(f.utf8), eVTC_UTF_8,
                reinterpret_cast<char *>(buffer.data()), capacity, eVTC_UTF_16);
            require(length == static_cast<PA_long32>(bytes), "returned charset length is bytes");
            require(CUTF16String(buffer.data(), length / sizeof(PA_Unichar)) == f.utf16,
                    "exact charset code units");
            require(buffer[capacity / sizeof(PA_Unichar)] == 0xabab, "capacity guard intact");
            if (capacity > bytes)
                require(buffer[f.utf16.size()] == 0, "terminator only within capacity");
        }
    }
    balanced();
}

static void arraysCase()
{
    PA_Variable array = PA_CreateVariable(eVK_ArrayUnicode);
    PA_ResizeArray(&array, 4);
    const char *fixtures[] = {"ascii", "\xE6\x97\xA5", "\xF0\x9F\x98\x80", "", ""};
    for (int i = 0; i <= 4; ++i) {
        CUTF16String s = utf16(fixtures[i]);
        PA_Unistring owned = PA_CreateUnistring(const_cast<PA_Unichar *>(s.c_str()));
        PA_SetStringInArray(array, i, &owned);
    }
    ARRAY_TEXT copy;
    uint8_t *params[] = {reinterpret_cast<uint8_t *>(&array)};
    copy.fromParamAtIndex(params, 1);
    // Copies must remain independent after the borrowed array elements are freed.
    PA_ClearVariable(&array);
    copy.toParamAtIndex(params, 1);
    for (int i = 0; i <= 4; ++i)
        require(text(PA_GetStringInArray(array, i)) == utf16(fixtures[i]), "exact array content");
    copy.toParamAtIndex(params, 1); // replacement disposes old elements, not the new owners
    PA_Unistring borrowed = PA_GetStringInArray(array, 1);
    PA_Unistring independent = PA_CreateUnistring(borrowed.fString);
    PA_SetStringInArray(array, 2, &independent);
    PA_ResizeArray(&array, 2);
    require(text(PA_GetStringInArray(array, 2)) == utf16(fixtures[1]), "independent transfer");
    PA_ClearVariable(&array);
    // Actual SDK rejection predicates: wrong kind, null data, negative/high index, null input.
    for (int which = 0; which < 4; ++which) {
        PA_Variable destination = PA_CreateVariable(eVK_ArrayUnicode);
        PA_Unistring owner = PA_CreateUnistring(const_cast<PA_Unichar *>(utf16("reject").c_str()));
        PA_Variable rejected = destination;
        if (which == 0) rejected.fType = eVK_Longint;
        if (which == 1) rejected.uValue.fArray.fData = nullptr;
        PA_SetStringInArray(rejected, which == 2 ? -1 : (which == 3 ? 1 : 0), &owner);
        require(text(owner) == utf16("reject"), "rejection retains caller owner");
        PA_DisposeUnistring(&owner);
        PA_SetStringInArray(destination, 0, nullptr);
        PA_ClearVariable(&destination);
    }
    balanced();
}

static void scalarCase()
{
    for (const char *s : {"ascii", "\xE6\x97\xA5", "\xF0\x9F\x98\x80", ""}) {
        CUTF16String fixture = utf16(s);
        C_TEXT copy;
        PA_Unistring input = PA_CreateUnistring(const_cast<PA_Unichar *>(fixture.c_str()));
        uint8_t *params[] = {reinterpret_cast<uint8_t *>(&input)};
        copy.fromParamAtIndex(params, 1);
        PA_DisposeUnistring(&input);
        input = PA_CreateUnistring(nullptr);
        copy.toParamAtIndex(params, 1);
        require(text(input) == fixture, "scalar copied borrowed content");
        PA_Unistring output = PA_CreateUnistring(nullptr);
        PA_Unistring *outputPtr = &output;
        copy.setReturn(reinterpret_cast<sLONG_PTR *>(&outputPtr));
        require(text(output) == fixture, "content setter return");
        PA_DisposeUnistring(&input);
        PA_DisposeUnistring(&output);
        expectedText = fixture;
        PA_Variable result = PA_ExecuteMethodByID(1, nullptr, 0);
        require(result.fType == eVK_Unistring, "method result kind");
        require(text(result.uValue.fString) == fixture, "owning method return");
        PA_ClearVariable(&result);
    }
    failHandle = true;
    PA_Variable command = PA_ExecuteCommandByID(1, nullptr, 0);
    require(command.fType == 0 && PA_GetVariableKind(command) == eVK_Unistring,
            "raw command failure sentinel must not be cleared as a string");
    PA_Variable method = PA_ExecuteMethodByID(1, nullptr, 0);
    require(method.fType == eVK_Undefined, "method failure kind");
    failHandle = false;
    balanced();
}

static void failuresCase()
{
    for (bool allocationFailure : {false, true}) {
        {
            CPicture p;
            failHandle = allocationFailure;
            failCommand = !allocationFailure;
            unsigned w, h;
            p.getSize(&w, &h);
            require(w == 0 && h == 0, "failed command leaves initialized scalar outputs");
            PA_Picture result = p.createGrayScale();
            if (result) livePicture(result);
            PA_DisposePicture(result);
            result = p.createThumbnail(8, 9);
            PA_DisposePicture(result);
            expectedText = utf16(".png");
            result = p.createCopy(".png");
            PA_DisposePicture(result);
            CUTF8String path(reinterpret_cast<const uint8_t *>("synthetic.png"));
            p.fromFileAtPath(&path);
            failHandle = failCommand = false;
        }
        balanced();
    }
}

static void dispatcherCase()
{
    PackInitBlock init = {};
    init.fCall4Dex = engine;
    FourDPackex(kInitPlugin, &init, nullptr, nullptr);
    require(init.fSupportedVersion == 0x1400, "actual exported entry initialization");
    for (bool state : {false, true}) {
        active = state;
        PA_long32 result = -1;
        FourDPackex(1, nullptr, nullptr, &result);
        require(result == state, "actual scalar dispatcher result");
    }
    FourDPackex(2, nullptr, nullptr, nullptr);
    require(activationCalls == 1, "activation uses only synthetic application");
    FourDPackex(999, nullptr, nullptr, nullptr);
    balanced();
}

int main(int argc, char **argv)
{
    @autoreleasepool {
        require(argc == 3, "case and empty representation required");
        gCall4D = engine;
        nullEmpty = strcmp(argv[2], "null") == 0;
        std::string name = argv[1];
        if (name == "path") pathCase();
        else if (name == "path-initial") pathOwnerCase(true);
        else if (name == "path-output") pathOwnerCase(false);
        else if (name == "bytes") bytesCase();
        else if (name == "gray" || name == "size" || name == "thumbnail" ||
                 name == "copy" || name == "copy-empty")
            helpersCase(name);
        else if (name == "handoffs") handoffsCase();
        else if (name == "arrays") arraysCase();
        else if (name == "scalar") scalarCase();
        else if (name == "charset") charsetCase();
        else if (name == "failures") failuresCase();
        else if (name == "dispatcher") dispatcherCase();
        else require(false, "unknown test");
        printf("PASS %s %s (strings=0 pictures=0 handles=0)\n", argv[1], argv[2]);
    }
}
