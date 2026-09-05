"""
RapidOCR 解析器 - 纯OCR文字识别

使用 RapidOCR (PP-OCRv5) 进行文字识别
"""

import os
import tempfile
import time
from pathlib import Path

import fitz
import numpy as np
from PIL import Image
from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR

from yuxi.knowledge.parser.base import BaseDocumentProcessor, OCRException
from yuxi.utils import logger


class RapidOCRParser(BaseDocumentProcessor):
    """RapidOCR 解析器 - 使用 ONNX 模型进行文字识别"""

    def __init__(self, det_box_thresh: float = 0.3):
        self.ocr = None
        self.det_box_thresh = det_box_thresh

    def get_service_name(self) -> str:
        return "rapid_ocr"

    def get_supported_extensions(self) -> list[str]:
        return [".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"]

    def _get_model_params(self) -> dict[str, object]:
        return {
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.lang_type": LangDet.CH,
            "Det.model_type": ModelType.MOBILE,
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Det.box_thresh": self.det_box_thresh,
            "Cls.engine_type": EngineType.ONNXRUNTIME,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": LangRec.CH,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
        }

    def check_health(self) -> dict:
        """检查 RapidOCR 模型是否可用"""
        try:
            test_ocr = RapidOCR(params=self._get_model_params())
            del test_ocr
            return {
                "status": "healthy",
                "message": "RapidOCR PP-OCRv5 模型可用",
                "details": {"ocr_version": "PP-OCRv5", "engine": "onnxruntime"},
            }
        except Exception as e:
            return {"status": "error", "message": f"模型加载失败: {str(e)}", "details": {"error": str(e)}}

    def _load_model(self):
        """延迟加载 OCR 模型"""
        if self.ocr is not None:
            return

        logger.info("加载 RapidOCR 模型...")

        try:
            self.ocr = RapidOCR(params=self._get_model_params())
            logger.info(f"RapidOCR PP-OCRv5 模型加载成功 (det_box_thresh={self.det_box_thresh})")
        except Exception as e:
            raise OCRException(f"RapidOCR模型加载失败: {str(e)}", self.get_service_name(), "load_failed")

    def process_image_result(self, image, params: dict | None = None) -> dict:
        """
        处理单张图像并返回可持久化的结构化结果

        Args:
            image: 图像数据,支持:
                  - str: 图像文件路径
                  - PIL.Image: PIL图像对象
                  - numpy.ndarray: numpy图像数组
            params: 处理参数 (当前未使用)

        Returns:
            dict: 文本、文字块和处理耗时
        """
        self._load_model()
        runtime_params = {
            key: value
            for key, value in (params or {}).items()
            if key in {"text_score", "box_thresh", "unclip_ratio"} and value is not None
        }

        try:
            if isinstance(image, (str, bytes, Path)):
                image_input = image
                cleanup_needed = False
            else:
                image_input = self._create_temp_image_file(image)
                cleanup_needed = True

            try:
                start_time = time.time()
                result = self.ocr(image_input, **runtime_params)
                processing_time = time.time() - start_time
                texts = list(result.txts or [])
                scores = list(result.scores or [])
                boxes = result.boxes.tolist() if result.boxes is not None else []
                blocks = [
                    {
                        "text": text,
                        "confidence": round(float(scores[index]), 6) if index < len(scores) else None,
                        "box": boxes[index] if index < len(boxes) else None,
                    }
                    for index, text in enumerate(texts)
                ]
                source_name = os.path.basename(image_input) if isinstance(image_input, str) else "memory_image"
                if texts:
                    logger.info(f"RapidOCR 成功: {source_name} ({processing_time:.2f}s)")
                else:
                    logger.warning(f"RapidOCR 未识别到文本: {source_name}")
                return {
                    "text": "\n".join(texts),
                    "blocks": blocks,
                    "processing_ms": round(processing_time * 1000),
                }

            finally:
                if cleanup_needed and os.path.exists(image_input):
                    try:
                        os.remove(image_input)
                    except Exception as e:
                        logger.warning(f"临时文件清理失败: {image_input} - {e}")

        except Exception as e:
            error_msg = f"图像OCR处理失败: {str(e)}"
            logger.error(error_msg)
            raise OCRException(error_msg, self.get_service_name(), "processing_failed")

    def process_image(self, image, params: dict | None = None) -> str:
        """处理单张图像并提取纯文本，保持现有文档解析接口兼容。"""
        return self.process_image_result(image, params)["text"]

    def _create_temp_image_file(self, image) -> str:
        """将图像数据保存为临时文件"""
        try:
            # 使用系统临时目录
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".png", delete=False) as tmp_file:
                temp_path = tmp_file.name

                if isinstance(image, Image.Image):
                    image.save(temp_path)
                elif isinstance(image, np.ndarray):
                    Image.fromarray(image).save(temp_path)
                else:
                    raise ValueError("不支持的图像类型,必须是 PIL.Image 或 numpy.ndarray")

                return temp_path

        except Exception as e:
            raise OCRException(f"临时图像文件创建失败: {str(e)}", self.get_service_name(), "temp_file_error")

    def process_pdf(self, pdf_path: str, params: dict | None = None) -> str:
        """
        处理 PDF 文件并提取文本 (流式处理,避免内存占用)

        Args:
            pdf_path: PDF 文件路径
            params: 处理参数
                - zoom_x: 横向缩放 (默认 2)
                - zoom_y: 纵向缩放 (默认 2)

        Returns:
            str: 提取的文本
        """
        if not os.path.exists(pdf_path):
            raise OCRException(f"PDF 文件不存在: {pdf_path}", self.get_service_name(), "file_not_found")

        params = params or {}
        zoom_x = params.get("zoom_x", 2)
        zoom_y = params.get("zoom_y", 2)

        try:
            all_text = []
            pdf_doc = fitz.open(pdf_path)
            total_pages = pdf_doc.page_count

            logger.info(f"开始处理 PDF: {os.path.basename(pdf_path)} ({total_pages} 页)")

            # 流式处理每一页,避免一次性加载所有图片到内存
            for page_num in range(total_pages):
                page = pdf_doc[page_num]

                # 转换为图像
                mat = fitz.Matrix(zoom_x, zoom_y)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img_pil = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                # 立即处理,不保存到列表
                text = self.process_image(img_pil)
                all_text.append(text)

                if (page_num + 1) % 10 == 0:
                    logger.info(f"已处理 {page_num + 1}/{total_pages} 页")

            pdf_doc.close()

            result_text = "\n\n".join(all_text)
            logger.info(f"PDF OCR 完成: {os.path.basename(pdf_path)} - {len(result_text)} 字符")
            return result_text

        except OCRException:
            raise
        except Exception as e:
            error_msg = f"PDF OCR 处理失败: {str(e)}"
            logger.error(error_msg)
            raise OCRException(error_msg, self.get_service_name(), "pdf_processing_failed")

    def process_file(self, file_path: str, params: dict | None = None) -> str:
        """
        处理文件 (PDF 或图像)

        Args:
            file_path: 文件路径
            params: 处理参数

        Returns:
            str: 提取的文本
        """
        file_ext = Path(file_path).suffix.lower()

        if not self.supports_file_type(file_ext):
            raise OCRException(f"不支持的文件类型: {file_ext}", self.get_service_name(), "unsupported_file_type")

        if file_ext == ".pdf":
            return self.process_pdf(file_path, params)
        else:
            return self.process_image(file_path, params)
