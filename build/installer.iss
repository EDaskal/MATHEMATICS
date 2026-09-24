; Inno Setup — πρόγραμμα εγκατάστασης για τη Γεννήτρια Διαγωνισμάτων.
; Χτίζεται αυτόματα στο GitHub: iscc /DAppVersion=1.0.0 build\installer.iss

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6C8F2B1E-4D7A-4E2B-9A61-5B0E7D3C2F18}
AppName=Γεννήτρια Διαγωνισμάτων
AppVersion={#AppVersion}
AppPublisher=Diagonismata
DefaultDirName={localappdata}\Programs\Diagonismata
DefaultGroupName=Γεννήτρια Διαγωνισμάτων
DisableProgramGroupPage=yes
; Εγκατάσταση μόνο για τον χρήστη: δεν ζητάει δικαιώματα διαχειριστή
PrivilegesRequired=lowest
OutputDir=..\build\dist
OutputBaseFilename=Diagonismata-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\Diagonismata.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
#if FileExists(AddBackslash(SourcePath) + "Greek.isl")
Name: "greek"; MessagesFile: "Greek.isl"
#else
Name: "english"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "Εικονίδιο στην επιφάνεια εργασίας"; GroupDescription: "Επιπλέον:"

[Files]
Source: "dist\Diagonismata\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Γεννήτρια Διαγωνισμάτων"; Filename: "{app}\Diagonismata.exe"
Name: "{autodesktop}\Γεννήτρια Διαγωνισμάτων"; Filename: "{app}\Diagonismata.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Diagonismata.exe"; Description: "Εκκίνηση τώρα"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
