// Teams (msol) widget: one click shows or hides Microsoft Teams, or starts it.
// Teams itself runs in msol's Edge; hidden means minimised and left out of the
// taskbar, so it keeps running (and notifying) in the background.
import QtQuick
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasma5support as P5Support

PlasmoidItem {
    id: root

    property string appId: "teams"
    property string iconSource: "im-user"
    property bool startAtLogin: false

    preferredRepresentation: compactRepresentation
    toolTipMainText: "Teams"
    toolTipSubText: "Click to show or hide Teams. Right-click for more."

    function msol(args) {
        runner.connectSource("msol " + args)
    }

    P5Support.DataSource {
        id: runner
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            const out = (data["stdout"] || "").trim()
            if (source.indexOf(" icon ") >= 0 && out.length > 0) {
                root.iconSource = out
            } else if (source.indexOf(" autostart ") >= 0) {
                root.startAtLogin = out === "on"
            }
            disconnectSource(source)
        }
    }

    compactRepresentation: Kirigami.Icon {
        source: root.iconSource
        active: mouse.containsMouse
        MouseArea {
            id: mouse
            anchors.fill: parent
            hoverEnabled: true
            onClicked: root.msol("window " + root.appId + " toggle")
        }
    }

    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: "Show Teams"
            icon.name: "window-restore"
            onTriggered: root.msol("window " + root.appId + " show")
        },
        PlasmaCore.Action {
            text: "Hide Teams"
            icon.name: "window-minimize"
            onTriggered: root.msol("window " + root.appId + " hide")
        },
        PlasmaCore.Action {
            text: "Quit Teams"
            icon.name: "application-exit"
            onTriggered: root.msol("window " + root.appId + " close")
        },
        PlasmaCore.Action {
            text: "Start Teams at login (hidden)"
            checkable: true
            checked: root.startAtLogin
            onTriggered: root.msol("autostart " + root.appId + (checked ? " on" : " off"))
        }
    ]

    Component.onCompleted: {
        msol("icon " + appId)
        msol("autostart " + appId + " status")
    }
}
