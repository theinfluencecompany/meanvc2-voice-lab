import { createRoot } from "react-dom/client";
import { LocalVoiceLab } from "./local-voice-lab";
import { SameSourceLab } from "./same-source-lab";
import { FishModelLab } from "./fish-model-lab";
import "./styles.css";
const view = new URLSearchParams(window.location.search).get("view");
createRoot(document.getElementById("root")!).render(view === "fish" ? <FishModelLab /> : view === "compare" ? <SameSourceLab /> : <LocalVoiceLab />);
