#!/usr/bin/env python3
"""
QCAD CV Matcher v2 — Computer vision object recognition for CAD drawings.

Replaces VLM visual matching with fast, deterministic CV:
  • Edge-map template matching (robust to color/style differences)
  • ORB/SIFT feature matching with RANSAC geometric verification
  • Multi-scale pyramid search
  • False-positive rejection via local consistency check

Usage:
    python qcad_cv_matcher.py --pdf-image /tmp/task_1_context.png --window-id 8389859
    python qcad_cv_matcher.py --tasks /tmp/pdf_contexts/manifest.json --report /tmp/cv_matches.json
"""

import os
import sys
import json
import math
import time
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from x11_controller import X11Controller


@dataclass
class MatchResult:
    target_found: bool
    screen_x: int = 0
    screen_y: int = 0
    confidence: float = 0.0
    method: str = ""
    scale: float = 1.0
    matched_size: Tuple[int, int] = (0, 0)
    reasoning: str = ""
    qcad_screenshot: str = ""
    raw_scores: Dict[str, float] = None


class QCADCVMatcher:
    """CV-based matching optimized for CAD drawings."""

    def __init__(
        self,
        method: str = "hybrid",
        confidence_threshold: float = 0.35,
        edge_low: int = 50,
        edge_high: int = 150,
    ):
        self.method = method
        self.confidence_threshold = confidence_threshold
        self.edge_low = edge_low
        self.edge_high = edge_high
        self.x11 = X11Controller()

    def _load(self, path: str) -> np.ndarray:
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise RuntimeError(f"Cannot load image: {path}")
        return img

    def _edges(self, gray: np.ndarray) -> np.ndarray:
        """Canny edge detection — robust to color/style differences."""
        return cv2.Canny(gray, self.edge_low, self.edge_high)

    # ═══════════════════════════════════════════════════════════
    # Method 1: Edge-Map Template Matching
    # ═══════════════════════════════════════════════════════════
    def match_template_edges(
        self,
        template_gray: np.ndarray,
        scene_gray: np.ndarray,
        num_scales: int = 15,
        scale_range: Tuple[float, float] = (0.3, 2.0),
    ) -> Optional[Tuple[int, int, float, float, Tuple[int, int]]]:
        """
        Match Canny edge maps at multiple scales.
        Returns (center_x, center_y, confidence, scale, (w, h)) or None.
        """
        t_edges = self._edges(template_gray)
        s_edges = self._edges(scene_gray)

        t_h, t_w = t_edges.shape
        s_h, s_w = s_edges.shape

        if t_w > s_w or t_h > s_h:
            return None

        best_score = -1.0
        best = None
        scales = np.geomspace(scale_range[0], scale_range[1], num_scales)

        for scale in scales:
            rw = max(1, int(t_w * scale))
            rh = max(1, int(t_h * scale))
            if rw > s_w or rh > s_h:
                continue

            resized = cv2.resize(t_edges, (rw, rh), interpolation=cv2.INTER_AREA)
            result = cv2.matchTemplate(s_edges, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > best_score:
                best_score = max_val
                cx = max_loc[0] + rw // 2
                cy = max_loc[1] + rh // 2
                best = (cx, cy, float(max_val), scale, (rw, rh))

        if best and best[2] >= self.confidence_threshold:
            return best
        return None

    # ═══════════════════════════════════════════════════════════
    # Method 2: Feature Matching (ORB/AKAZE + RANSAC)
    # ═══════════════════════════════════════════════════════════
    def match_features(
        self,
        template_gray: np.ndarray,
        scene_gray: np.ndarray,
        detector: str = "ORB",
    ) -> Optional[Tuple[int, int, float]]:
        """Feature matching with homography verification."""
        if detector.upper() == "ORB":
            det = cv2.ORB_create(nfeatures=2000)
            bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        elif detector.upper() == "SIFT":
            det = cv2.SIFT_create()
            bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
        else:
            det = cv2.AKAZE_create()
            bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

        kp1, des1 = det.detectAndCompute(template_gray, None)
        kp2, des2 = det.detectAndCompute(scene_gray, None)

        if des1 is None or des2 is None or len(kp1) < 4 or len(kp2) < 4:
            return None

        knn = bf.knnMatch(des1, des2, k=2)
        good = [m for m, n in knn if m.distance < 0.75 * n.distance]

        if len(good) < 10:
            return None

        src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

        H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        if H is None or mask is None:
            return None

        inliers = int(mask.sum())
        inlier_ratio = inliers / len(good)

        t_h, t_w = template_gray.shape[:2]
        center = np.float32([[t_w / 2.0, t_h / 2.0]]).reshape(-1, 1, 2)
        proj = cv2.perspectiveTransform(center, H)
        sx, sy = int(proj[0][0][0]), int(proj[0][0][1])

        # Reject if projected point is outside scene or near borders (toolbars)
        margin = 30
        if sx < margin or sy < margin or sx >= s_w - margin or sy >= s_h - margin:
            # Still return but with reduced confidence
            inlier_ratio *= 0.3

        conf = min(1.0, inlier_ratio * 1.5)
        if inliers < 10:
            conf *= 0.5

        if conf >= self.confidence_threshold:
            return (sx, sy, conf)
        return None

    # ═══════════════════════════════════════════════════════════
    # Method 3: Local Consistency Check
    # ═══════════════════════════════════════════════════════════
    def local_verify(
        self,
        template_gray: np.ndarray,
        scene_gray: np.ndarray,
        cx: int,
        cy: int,
        scale: float,
    ) -> float:
        """
        Verify a match by comparing local image patches.
        Extract a region around (cx,cy) in the scene and compare to template.
        Returns similarity score 0-1.
        """
        t_h, t_w = template_gray.shape
        s_h, s_w = scene_gray.shape

        # Region size in scene (scaled)
        region_w = int(t_w * scale)
        region_h = int(t_h * scale)

        x0 = max(0, cx - region_w // 2)
        y0 = max(0, cy - region_h // 2)
        x1 = min(s_w, x0 + region_w)
        y1 = min(s_h, y0 + region_h)

        if x1 - x0 < 10 or y1 - y0 < 10:
            return 0.0

        region = scene_gray[y0:y1, x0:x1]
        # Resize template to match region
        tmpl_resized = cv2.resize(template_gray, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)

        # Compute structural similarity on edge maps
        reg_edges = self._edges(region)
        tmpl_edges = self._edges(tmpl_resized)

        # Normalize and compare
        reg_norm = reg_edges.astype(np.float32) / 255.0
        tmpl_norm = tmpl_edges.astype(np.float32) / 255.0

        # Correlation coefficient
        reg_mean = reg_norm.mean()
        tmpl_mean = tmpl_norm.mean()
        reg_std = reg_norm.std()
        tmpl_std = tmpl_norm.std()

        if reg_std < 1e-6 or tmpl_std < 1e-6:
            return 0.0

        corr = ((reg_norm - reg_mean) * (tmpl_norm - tmpl_mean)).mean() / (reg_std * tmpl_std)
        corr = max(0.0, min(1.0, (corr + 1) / 2))  # Map [-1,1] -> [0,1]

        return corr

    # ═══════════════════════════════════════════════════════════
    # Main Match
    # ═══════════════════════════════════════════════════════════
    def match(
        self,
        pdf_image_path: str,
        qcad_image_path: str,
        instruction: str = "",
    ) -> MatchResult:
        """Find PDF context inside QCAD screenshot."""
        try:
            template_gray = self._load(pdf_image_path)
            scene_gray = self._load(qcad_image_path)
        except Exception as e:
            return MatchResult(
                target_found=False,
                reasoning=f"Load error: {e}",
                qcad_screenshot=qcad_image_path,
            )

        global s_h, s_w
        s_h, s_w = scene_gray.shape

        raw_scores: Dict[str, float] = {}
        candidates: List[Tuple[str, Tuple[int, int], float, str, float, Tuple[int, int]]] = []

        # ── Method A: Edge-Map Template ──
        if self.method in ("template", "hybrid"):
            start = time.time()
            tmpl = self.match_template_edges(template_gray, scene_gray)
            elapsed = time.time() - start
            if tmpl:
                x, y, conf, scale, size = tmpl
                raw_scores["edge_template"] = conf
                # Local verify
                verify = self.local_verify(template_gray, scene_gray, x, y, scale)
                combined = conf * 0.5 + verify * 0.5  # 50/50 for CAD edge matching
                candidates.append(("edge_template", (x, y), combined,
                                   f"Edge-template conf={conf:.3f} verify={verify:.3f} scale={scale:.2f} in {elapsed:.2f}s",
                                   scale, size))

        # ── Method B: Feature Matching ──
        if self.method in ("feature", "hybrid"):
            for det_name in ["ORB", "AKAZE"]:
                start = time.time()
                feat = self.match_features(template_gray, scene_gray, detector=det_name)
                elapsed = time.time() - start
                if feat:
                    x, y, conf = feat
                    key = f"feature_{det_name.lower()}"
                    raw_scores[key] = conf
                    # For features, use a nominal scale of 1.0
                    candidates.append((key, (x, y), conf,
                                       f"{det_name} feature conf={conf:.3f} in {elapsed:.2f}s",
                                       1.0, (0, 0)))

        # ── Pick Best ──
        if not candidates:
            return MatchResult(
                target_found=False,
                reasoning="No match found",
                qcad_screenshot=qcad_image_path,
                raw_scores=raw_scores,
            )

        # Sort by confidence descending
        candidates.sort(key=lambda c: c[2], reverse=True)
        best = candidates[0]

        method_name, (bx, by), conf, reason, scale, size = best

        # Cross-check: if feature match disagrees with template match, reduce confidence
        if len(candidates) >= 2:
            second = candidates[1]
            dist = math.hypot(bx - second[1][0], by - second[1][1])
            if dist > 50:  # Methods disagree significantly
                conf *= 0.8
                reason += f" [methods diverge by {dist:.0f}px]"

        # Reject matches near window borders (likely toolbars/menus)
        margin = 40
        if bx < margin or by < margin or bx > s_w - margin or by > s_h - margin:
            conf *= 0.5
            reason += " [near border]"

        return MatchResult(
            target_found=conf >= self.confidence_threshold,
            screen_x=bx,
            screen_y=by,
            confidence=conf,
            method=method_name,
            scale=scale,
            matched_size=size,
            reasoning=reason,
            qcad_screenshot=qcad_image_path,
            raw_scores=raw_scores,
        )

    # ═══════════════════════════════════════════════════════════
    # Batch / Manifest Processing
    # ═══════════════════════════════════════════════════════════
    def capture_qcad(self, window_id: Optional[int] = None) -> str:
        if window_id is None:
            for name in ["QCAD", "QCAD Professional", "QCAD Trial", "QCAD 3", "RivieraWaves"]:
                window_id = self.x11.get_window_by_name(name)
                if window_id:
                    break
        if not window_id:
            raise RuntimeError("QCAD window not found")
        path = f"/tmp/qcad_screenshot_{int(time.time())}.png"
        self.x11.screenshot_window(window_id, path)
        return path

    def process_manifest(
        self,
        manifest_path: str,
        window_id: Optional[int] = None,
        report_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        with open(manifest_path) as f:
            manifest = json.load(f)

        contexts = manifest.get("contexts", [])
        results = []

        print(f"CV matching {len(contexts)} tasks (method={self.method}, threshold={self.confidence_threshold})")
        print("=" * 60)

        qcad_image = self.capture_qcad(window_id)
        print(f"QCAD screenshot: {qcad_image}\n")

        for ctx in contexts:
            tid = ctx["task_id"]
            instruction = ctx["instruction"]
            pdf_image = ctx["image_path"]

            print(f"[{tid}] {instruction}")
            start = time.time()
            mr = self.match(pdf_image, qcad_image, instruction)
            elapsed = time.time() - start

            results.append({
                "task_id": tid,
                "instruction": instruction,
                "action_type": ctx["action_type"],
                "target_found": mr.target_found,
                "coordinates": (mr.screen_x, mr.screen_y),
                "confidence": round(mr.confidence, 4),
                "method": mr.method,
                "reasoning": mr.reasoning,
                "duration_ms": int(elapsed * 1000),
            })

            if mr.target_found:
                print(f"  ✅ ({mr.screen_x}, {mr.screen_y}) conf={mr.confidence:.3f} [{mr.method}]")
            else:
                print(f"  ❌ {mr.reasoning}")
                if mr.raw_scores:
                    print(f"     raw: {mr.raw_scores}")
            print()

        report = {
            "total_tasks": len(results),
            "found": sum(1 for r in results if r["target_found"]),
            "not_found": len(results) - sum(1 for r in results if r["target_found"]),
            "qcad_screenshot": qcad_image,
            "method": self.method,
            "threshold": self.confidence_threshold,
            "results": results,
        }

        if report_path:
            Path(report_path).parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, 'w') as f:
                json.dump(report, f, indent=2)
            print(f"Report saved: {report_path}")

        return report

    def close(self):
        self.x11.close()


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="QCAD CV Matcher (edge + feature)")
    parser.add_argument("--pdf-image", help="PDF context image")
    parser.add_argument("--qcad-image", help="Existing QCAD screenshot")
    parser.add_argument("--window-id", type=int, help="QCAD window ID")
    parser.add_argument("--tasks", "-t", help="manifest.json for batch")
    parser.add_argument("--report", "-r", help="Save JSON report")
    parser.add_argument("--method", default="hybrid",
                        choices=["template", "feature", "hybrid"])
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument("--visualize", action="store_true")
    args = parser.parse_args()

    matcher = QCADCVMatcher(method=args.method, confidence_threshold=args.threshold)

    try:
        if args.tasks:
            if not Path(args.tasks).exists():
                print(f"ERROR: Manifest not found: {args.tasks}")
                sys.exit(1)
            report = matcher.process_manifest(args.tasks, args.window_id, args.report)
            print(f"\nMatched: {report['found']}/{report['total_tasks']}")
        elif args.pdf_image:
            if not Path(args.pdf_image).exists():
                print(f"ERROR: Image not found: {args.pdf_image}")
                sys.exit(1)

            qcad_img = args.qcad_image or matcher.capture_qcad(args.window_id)
            if not args.qcad_image:
                print(f"QCAD screenshot: {qcad_img}")

            print(f"\nMatching: {args.method} (threshold={args.threshold})")
            result = matcher.match(args.pdf_image, qcad_img)

            print(f"\n{'='*60}")
            print(f"Found:    {result.target_found}")
            print(f"Coords:   ({result.screen_x}, {result.screen_y})")
            print(f"Conf:     {result.confidence:.3f}")
            print(f"Method:   {result.method}")
            print(f"Reason:   {result.reasoning}")
            if result.raw_scores:
                print(f"Raw:      {result.raw_scores}")

            if args.visualize and result.target_found:
                scene = cv2.imread(qcad_img)
                if scene is not None:
                    cv2.circle(scene, (result.screen_x, result.screen_y), 20, (0, 255, 0), 3)
                    cv2.imshow("CV Match", scene)
                    print("\nPress any key to close...")
                    cv2.waitKey(0)
                    cv2.destroyAllWindows()
        else:
            print("ERROR: Provide --tasks or --pdf-image")
            sys.exit(1)
    finally:
        matcher.close()


if __name__ == "__main__":
    main()
