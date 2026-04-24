{
  description = "uv flake";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
    unstable-nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };
  outputs = { self, nixpkgs, unstable-nixpkgs, flake-utils}:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system ;
          config = {
            allowUnfree = true;
            cudaSupport = true;
          };
        };
        unstable-pkgs = import unstable-nixpkgs {
          inherit system;
          config = {
            allowUnfree = true;
            cudaSupport = true;
          };
        };

        cudatoolkit = pkgs.cudaPackages_12.cudatoolkit;
        uvFHSenv = pkgs.buildFHSEnv {
          name = "uv-env";
          runScript = "bash";

	  targetPkgs = pkgs:[
            pkgs.python314
            pkgs.uv
            pkgs.cmake
            pkgs.ninja
            pkgs.tree-sitter
            cudatoolkit
            pkgs.nixd
            pkgs.nil
            pkgs.ruff
            pkgs.gcc
            pkgs.zlib
            pkgs.ffmpeg
            
            pkgs.xorg.libxcb
            pkgs.xorg.libX11
            pkgs.glib
            pkgs.libGL
           ];


           profile = ''
             export UV_PYTHON=python3.14
             
             if [ ! -d ".venv" ]; then
                echo "Creating Python 3.14 virtual environment..."
                uv venv .venv --python python3.14
             fi
             source .venv/bin/activate
             
             echo "Environment created"
	     '';
        };
      in
      {
        devShells.default = uvFHSenv.env; 
      });
}
