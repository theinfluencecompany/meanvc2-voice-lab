import { createRoot } from "react-dom/client";
import { LocalVoiceLab } from "./local-voice-lab";
import { SameSourceLab } from "./same-source-lab";
import "./styles.css";
createRoot(document.getElementById("root")!).render(new URLSearchParams(window.location.search).get("view") === "compare" ? <SameSourceLab /> : <LocalVoiceLab />);
