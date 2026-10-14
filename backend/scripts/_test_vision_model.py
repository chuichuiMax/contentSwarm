import asyncio
import sys

from yuxi.image_design.analysis import run_visual_analysis

TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909"
    "080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c283729"
    "2c30313434341f27393d38323c2e333432ffdb0043010909090c0b0c180d0d1832211c21"
    "32323232323232323232323232323232323232323232323232323232323232323232323232"
    "323232323232323232323232ffc0001108000100010301110002110003110003ffc4001500"
    "0101000000000000000000000000000008ffc4001410010000000000000000000000000000"
    "0000ffda000801010000003f00ffd9"
)


async def main() -> None:
    spec = sys.argv[1] if len(sys.argv) > 1 else "siliconflow-cn:Qwen/Qwen3-VL-8B-Instruct"
    try:
        result, resolved = await run_visual_analysis(TINY_JPEG, "structure_source", model_spec=spec)
        print(f"OK resolved={resolved} room_type={result.room_type}")
    except Exception as exc:
        print(f"FAIL {type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
